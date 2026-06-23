"""Command line interface for the Altruist tester."""

import json
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol

import serial
import typer

from altruist_tester import __version__
from altruist_tester.artifacts import (
    BatchArtifacts,
    BatchDeviceArtifacts,
    create_batch_artifacts,
    create_run_artifacts,
    utc_now,
)
from altruist_tester.config import (
    BatchConfig,
    BatchDeviceConfig,
    ConfigError,
    TesterConfig,
    load_batch_config,
    load_tester_config,
    validate_batch_config,
)
from altruist_tester.duration import DurationParseError, parse_duration_seconds
from altruist_tester.identity import detect_device_identity, normalize_device_id
from altruist_tester.ports import SerialPortInfo, list_serial_ports
from altruist_tester.rules.engine import (
    RuleEngineConfig,
    RuleEngineResult,
    evaluate_rules,
)
from altruist_tester.rules.presence import (
    UnknownExpectedSensorError,
    expected_metrics_for_sensors,
)
from altruist_tester.serial_logger import SerialLogProgress, capture_raw_serial

DEFAULT_OUTPUT_DIR = Path("runs")
BATCH_PROGRESS_INTERVAL_SECONDS = 10.0
BATCH_TERMINATE_GRACE_SECONDS = 5.0


class BatchInterrupted(RuntimeError):
    """Raised when a batch run receives an external interruption signal."""


class RuleReportMessage(Protocol):
    """Report subset used for CLI status output."""

    status: str
    message: str


@dataclass(frozen=True, slots=True)
class BatchWorkerProcess:
    """One subprocess worker launched for a batch device slot."""

    slot: str
    command: list[str]
    output_dir: Path
    stdout_log: Path
    stderr_log: Path
    process: subprocess.Popen[str]


@dataclass(frozen=True, slots=True)
class BatchWorkerStartFailure:
    """A batch worker that could not be launched."""

    slot: str
    command: list[str]
    output_dir: Path
    stdout_log: Path
    stderr_log: Path
    error: str


app = typer.Typer(
    help=(
        "Post-assembly burn-in tester for Altruist devices. "
        "The initial workflow focuses on one USB-C serial device."
    ),
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    """Print the package version for Typer's eager ``--version`` option."""

    if value:
        typer.echo(f"altruist-tester {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            help="Show the installed tester version.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Run Altruist tester commands."""


def _format_port_info(port: SerialPortInfo) -> str:
    parts = [port.device]
    details = []
    if port.description:
        details.append(port.description)
    if port.manufacturer:
        details.append(port.manufacturer)
    if port.product and port.product != port.description:
        details.append(port.product)
    if port.vid_pid:
        details.append(f"VID:PID={port.vid_pid}")
    if port.serial_number:
        details.append(f"SER={port.serial_number}")
        device_id = normalize_device_id(port.serial_number)
        if device_id is not None:
            details.append(f"device_id={device_id}")
    if port.location:
        details.append(f"LOCATION={port.location}")
    if port.hwid:
        details.append(f"HWID={port.hwid}")
    if details:
        parts.append(" - " + "; ".join(details))
    return "".join(parts)


def _print_ports(port_infos: list[SerialPortInfo]) -> None:
    if not port_infos:
        typer.echo("No serial ports found.")
        return

    for port_info in port_infos:
        typer.echo(_format_port_info(port_info))


def _format_optional_path(path: Path | None) -> str:
    if path is None:
        return "<none>"
    return str(path)


def _format_batch_identity(port: Path, port_infos: list[SerialPortInfo]) -> str:
    identity = detect_device_identity(port, port_infos=port_infos)
    if identity.device_id is None and identity.usb_serial is None:
        return "<not detected>"

    parts = []
    if identity.device_id is not None:
        parts.append(f"device_id={identity.device_id}")
    if identity.mac is not None:
        parts.append(f"mac={identity.mac}")
    if identity.usb_serial is not None:
        parts.append(f"usb_serial={identity.usb_serial}")
    return ", ".join(parts)


def _print_batch_dry_run(config: BatchConfig) -> None:
    port_infos = list_serial_ports()

    typer.echo("Batch dry-run")
    typer.echo(f"- duration: {config.duration_input} ({config.duration_seconds}s)")
    typer.echo(f"- baud: {config.baud}")
    typer.echo(f"- output_dir: {config.output_dir}")
    typer.echo(f"- default_config: {_format_optional_path(config.device_config)}")
    typer.echo("Devices:")
    for device in config.devices:
        port_exists = "yes" if device.port.exists() else "no"
        typer.echo(f"  - {device.slot}")
        typer.echo(f"    model: {device.model or '<unspecified>'}")
        typer.echo(f"    port: {device.port}")
        typer.echo(f"    port_exists: {port_exists}")
        typer.echo(f"    config: {_format_optional_path(device.effective_config)}")
        typer.echo(f"    identity: {_format_batch_identity(device.port, port_infos)}")


def _explicit_batch_config(
    *,
    ports: list[Path] | None,
    duration: str | None,
    baud: int,
    output_dir: Path,
    device_config: Path | None,
) -> BatchConfig:
    if not ports:
        raise typer.BadParameter(
            "Specify --config or at least one --port.",
            param_hint="--port",
        )
    if duration is None:
        raise typer.BadParameter(
            "Specify --duration when using explicit --port mode.",
            param_hint="--duration",
        )
    if device_config is None:
        raise typer.BadParameter(
            "Specify --device-config when using explicit --port mode.",
            param_hint="--device-config",
        )

    try:
        duration_seconds = parse_duration_seconds(duration)
    except DurationParseError as exc:
        raise typer.BadParameter(str(exc), param_hint="--duration") from exc

    config = BatchConfig(
        duration_input=duration,
        duration_seconds=duration_seconds,
        baud=baud,
        output_dir=output_dir,
        device_config=device_config,
        devices=tuple(
            BatchDeviceConfig(
                slot=f"device-{index:02d}",
                port=port,
                effective_config=device_config,
            )
            for index, port in enumerate(ports, start=1)
        ),
    )
    try:
        validate_batch_config(config)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="--device-config") from exc
    return config


def _load_batch_cli_config(
    *,
    config: Path | None,
    ports: list[Path] | None,
    duration: str | None,
    baud: int,
    output_dir: Path,
    device_config: Path | None,
) -> BatchConfig:
    if config is not None:
        if ports or duration is not None or device_config is not None:
            raise typer.BadParameter(
                "Use either --config or explicit --port mode, not both.",
                param_hint="--config",
            )
        try:
            return load_batch_config(config)
        except ConfigError as exc:
            raise typer.BadParameter(str(exc), param_hint="--config") from exc

    return _explicit_batch_config(
        ports=ports,
        duration=duration,
        baud=baud,
        output_dir=output_dir,
        device_config=device_config,
    )


def _batch_worker_command(
    config: BatchConfig,
    device_artifacts: BatchDeviceArtifacts,
) -> list[str]:
    device = device_artifacts.device
    command = [
        sys.executable,
        "-m",
        "altruist_tester.cli",
        "run",
        "--port",
        str(device.port),
        "--duration",
        config.duration_input,
        "--baud",
        str(config.baud),
        "--output-dir",
        str(device_artifacts.output_dir),
    ]
    if device.effective_config is not None:
        command.extend(["--config", str(device.effective_config)])
    for sensor in device.expected_sensors:
        command.extend(["--expect-sensor", sensor])
    for metric in device.expected_metrics:
        command.extend(["--expect-metric", metric])
    return command


def _start_batch_worker(
    config: BatchConfig,
    device_artifacts: BatchDeviceArtifacts,
) -> BatchWorkerProcess:
    stdout_log = device_artifacts.output_dir / "worker.stdout.log"
    stderr_log = device_artifacts.output_dir / "worker.stderr.log"
    command = _batch_worker_command(config, device_artifacts)

    with stdout_log.open("w", encoding="utf-8") as stdout:
        with stderr_log.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )

    return BatchWorkerProcess(
        slot=device_artifacts.slot,
        command=command,
        output_dir=device_artifacts.output_dir,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        process=process,
    )


def _batch_worker_failure_kind(returncode: int | None) -> str | None:
    if returncode == 0:
        return None
    if returncode == 1:
        return "health_check_failed"
    if returncode == 2:
        return "infrastructure_or_config_failed"
    if returncode is None:
        return "worker_start_failed"
    return "worker_failed"


def _batch_worker_result(
    worker: BatchWorkerProcess,
    returncode: int,
) -> dict[str, object]:
    failure_kind = _batch_worker_failure_kind(returncode)
    return {
        "slot": worker.slot,
        "status": "completed" if returncode == 0 else "failed",
        "failure_kind": failure_kind,
        "returncode": returncode,
        "command": list(worker.command),
        "output_dir": str(worker.output_dir),
        "stdout_log": str(worker.stdout_log),
        "stderr_log": str(worker.stderr_log),
    }


def _batch_worker_start_failure_result(
    failure: BatchWorkerStartFailure,
) -> dict[str, object]:
    return {
        "slot": failure.slot,
        "status": "failed",
        "failure_kind": _batch_worker_failure_kind(None),
        "returncode": None,
        "command": list(failure.command),
        "output_dir": str(failure.output_dir),
        "stdout_log": str(failure.stdout_log),
        "stderr_log": str(failure.stderr_log),
        "error": failure.error,
    }


def _handle_batch_signal(signum, frame) -> None:
    raise BatchInterrupted(f"Received signal {signum}")


def _find_device_summary(output_dir: Path) -> Path | None:
    candidates = sorted(
        (
            child / "summary.json"
            for child in output_dir.iterdir()
            if child.is_dir() and (child / "summary.json").is_file()
        ),
        key=lambda path: path.parent.name,
    )
    if not candidates:
        return None
    return candidates[-1]


def _load_device_summary(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    if not isinstance(summary, dict):
        raise ValueError("summary.json must contain an object")
    return summary


def _device_identity_from_summary(summary: dict[str, object]) -> object:
    identity = summary.get("device_identity")
    if isinstance(identity, dict):
        return identity
    return None


def _findings_count(summary: dict[str, object]) -> int | None:
    findings = summary.get("findings")
    if isinstance(findings, list):
        return len(findings)
    return None


def _finding_messages(summary: dict[str, object], *, limit: int = 3) -> list[str]:
    findings = summary.get("findings")
    if not isinstance(findings, list):
        return []

    messages = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        message = finding.get("message")
        code = finding.get("code")
        severity = finding.get("severity")
        if isinstance(message, str) and message:
            if isinstance(code, str) and code:
                messages.append(f"{code}: {message}")
            elif isinstance(severity, str) and severity:
                messages.append(f"{severity}: {message}")
            else:
                messages.append(message)
        elif isinstance(code, str) and code:
            messages.append(code)
        if len(messages) >= limit:
            break
    return messages


def _device_result_from_summary(
    device_artifacts: BatchDeviceArtifacts,
    worker_result: dict[str, object],
    summary_path: Path,
    summary: dict[str, object],
) -> dict[str, object]:
    device = device_artifacts.device
    rules = summary.get("rules")
    failed_checks = []
    if isinstance(rules, dict):
        raw_failed_checks = rules.get("failed_checks")
        if isinstance(raw_failed_checks, list):
            failed_checks = raw_failed_checks

    return {
        "slot": device.slot,
        "model": device.model,
        "port": str(device.port),
        "config": str(device.effective_config) if device.effective_config else None,
        "worker_status": worker_result.get("status"),
        "worker_returncode": worker_result.get("returncode"),
        "failure_kind": worker_result.get("failure_kind"),
        "summary_json": str(summary_path),
        "report_txt": str(summary_path.parent / "report.txt"),
        "run_dir": summary.get("run_dir"),
        "status": summary.get("status"),
        "verdict": summary.get("verdict"),
        "device_identity": _device_identity_from_summary(summary),
        "findings_count": _findings_count(summary),
        "finding_messages": _finding_messages(summary),
        "failed_checks": failed_checks,
        "upload_health": summary.get("upload_health"),
        "sensor_presence": summary.get("sensor_presence"),
    }


def _device_result_without_summary(
    device_artifacts: BatchDeviceArtifacts,
    worker_result: dict[str, object],
    reason: str,
) -> dict[str, object]:
    device = device_artifacts.device
    return {
        "slot": device.slot,
        "model": device.model,
        "port": str(device.port),
        "config": str(device.effective_config) if device.effective_config else None,
        "worker_status": worker_result.get("status"),
        "worker_returncode": worker_result.get("returncode"),
        "failure_kind": worker_result.get("failure_kind"),
        "summary_json": None,
        "report_txt": None,
        "run_dir": None,
        "status": "failed",
        "verdict": None,
        "device_identity": None,
        "findings_count": None,
        "finding_messages": [],
        "failed_checks": [],
        "upload_health": None,
        "sensor_presence": None,
        "summary_error": reason,
    }


def _collect_device_result(
    device_artifacts: BatchDeviceArtifacts,
    worker_result: dict[str, object],
) -> dict[str, object]:
    summary_path = _find_device_summary(device_artifacts.output_dir)
    if summary_path is None:
        return _device_result_without_summary(
            device_artifacts,
            worker_result,
            "summary.json not found",
        )

    try:
        summary = _load_device_summary(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _device_result_without_summary(
            device_artifacts,
            worker_result,
            f"Could not read summary.json: {exc}",
        )

    return _device_result_from_summary(
        device_artifacts,
        worker_result,
        summary_path,
        summary,
    )


def _collect_device_results(
    artifacts_devices: tuple[BatchDeviceArtifacts, ...],
    worker_results: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    by_slot = {device.slot: device for device in artifacts_devices}
    return tuple(
        _collect_device_result(by_slot[str(result["slot"])], result)
        for result in worker_results
        if result.get("slot") in by_slot
    )


def _device_identity_value(device_result: dict[str, object], field: str) -> object:
    identity = device_result.get("device_identity")
    if isinstance(identity, dict):
        return identity.get(field)
    return None


def _batch_device_entry(device_result: dict[str, object]) -> dict[str, object]:
    return {
        "slot": device_result.get("slot"),
        "model": device_result.get("model"),
        "port": device_result.get("port"),
        "config": device_result.get("config"),
        "device_id": _device_identity_value(device_result, "device_id"),
        "mac": _device_identity_value(device_result, "mac"),
        "run_dir": device_result.get("run_dir"),
        "report_txt": device_result.get("report_txt"),
        "status": device_result.get("status"),
        "verdict": device_result.get("verdict"),
        "findings_count": device_result.get("findings_count"),
        "finding_messages": device_result.get("finding_messages"),
        "failed_checks": device_result.get("failed_checks"),
        "failure_kind": device_result.get("failure_kind"),
        "summary_error": device_result.get("summary_error"),
    }


def _is_failed_device(device_result: dict[str, object]) -> bool:
    if device_result.get("worker_returncode") not in (0, None):
        return True
    if device_result.get("failure_kind") == "worker_start_failed":
        return True
    if device_result.get("summary_error") is not None:
        return True
    if device_result.get("status") == "failed":
        return True
    return device_result.get("verdict") == "FAIL"


def _is_warned_device(device_result: dict[str, object]) -> bool:
    return device_result.get("verdict") == "WARN"


def _is_passed_device(device_result: dict[str, object]) -> bool:
    return (
        not _is_failed_device(device_result)
        and not _is_warned_device(device_result)
        and device_result.get("verdict") == "PASS_CANDIDATE"
    )


def _batch_aggregate(
    device_results: tuple[dict[str, object], ...],
) -> dict[str, object]:
    devices = [_batch_device_entry(device_result) for device_result in device_results]
    devices_failed = sum(
        1 for device_result in device_results if _is_failed_device(device_result)
    )
    devices_warned = sum(
        1 for device_result in device_results if _is_warned_device(device_result)
    )
    devices_passed = sum(
        1 for device_result in device_results if _is_passed_device(device_result)
    )
    verdict = "PASS_CANDIDATE"
    if devices_failed:
        verdict = "FAIL"
    elif devices_warned:
        verdict = "WARN"

    return {
        "verdict": verdict,
        "devices_total": len(device_results),
        "devices_passed": devices_passed,
        "devices_warned": devices_warned,
        "devices_failed": devices_failed,
        "devices": devices,
    }


def _batch_progress_line(
    *,
    elapsed_seconds: float,
    duration_seconds: int,
    running_count: int,
    completed_count: int,
    failed_count: int,
    batch_dir: Path,
) -> str:
    percent = 100.0
    if duration_seconds > 0:
        percent = min(100.0, elapsed_seconds / duration_seconds * 100.0)
    return (
        f"Batch {percent:5.1f}% "
        f"({_format_elapsed(elapsed_seconds)}/{_format_elapsed(duration_seconds)}) "
        f"| running={running_count} "
        f"completed={completed_count} "
        f"failed={failed_count} "
        f"| dir={batch_dir}"
    )


def _batch_slot_states(
    slots: tuple[str, ...],
    running_slots: set[str],
    worker_results: list[dict[str, object]],
) -> str:
    results_by_slot = {
        str(result["slot"]): result for result in worker_results if "slot" in result
    }
    states = []
    for slot in slots:
        if slot in running_slots:
            states.append(f"{slot}=running")
            continue
        result = results_by_slot.get(slot)
        if result is None:
            states.append(f"{slot}=pending")
        elif result.get("status") == "completed":
            states.append(f"{slot}=completed")
        else:
            failure_kind = result.get("failure_kind") or "failed"
            states.append(f"{slot}=failed({failure_kind})")
    return "Slots: " + " ".join(states)


def _emit_batch_progress(
    *,
    started_monotonic: float,
    now_monotonic: float,
    duration_seconds: int,
    batch_dir: Path,
    slots: tuple[str, ...],
    running_slots: set[str],
    worker_results: list[dict[str, object]],
) -> None:
    completed_count = sum(
        1 for result in worker_results if result["status"] == "completed"
    )
    failed_count = sum(1 for result in worker_results if result["status"] == "failed")
    typer.echo(
        _batch_progress_line(
            elapsed_seconds=now_monotonic - started_monotonic,
            duration_seconds=duration_seconds,
            running_count=len(running_slots),
            completed_count=completed_count,
            failed_count=failed_count,
            batch_dir=batch_dir,
        )
    )
    typer.echo(_batch_slot_states(slots, running_slots, worker_results))


def _worker_returncode(worker: BatchWorkerProcess) -> int | None:
    poll = getattr(worker.process, "poll", None)
    if callable(poll):
        return poll()
    return worker.process.wait()


def _process_current_returncode(process: subprocess.Popen[str]) -> int | None:
    poll = getattr(process, "poll", None)
    if callable(poll):
        return poll()
    return getattr(process, "returncode", None)


def _interrupted_worker_result(
    worker: BatchWorkerProcess,
    *,
    returncode: int | None,
) -> dict[str, object]:
    return {
        "slot": worker.slot,
        "status": "interrupted",
        "failure_kind": "interrupted",
        "returncode": returncode,
        "command": list(worker.command),
        "output_dir": str(worker.output_dir),
        "stdout_log": str(worker.stdout_log),
        "stderr_log": str(worker.stderr_log),
    }


def _unfinished_workers(
    workers: list[BatchWorkerProcess],
    worker_results: list[dict[str, object]],
) -> list[BatchWorkerProcess]:
    finished_slots = {
        str(result["slot"]) for result in worker_results if "slot" in result
    }
    return [
        worker
        for worker in workers
        if worker.slot not in finished_slots
        and _process_current_returncode(worker.process) is None
    ]


def _terminate_batch_workers(
    workers: list[BatchWorkerProcess],
    worker_results: list[dict[str, object]],
) -> None:
    running_workers = _unfinished_workers(workers, worker_results)
    if not running_workers:
        return

    typer.echo("Stopping running batch workers...", err=True)
    for worker in running_workers:
        terminate = getattr(worker.process, "terminate", None)
        if callable(terminate):
            terminate()

    deadline = time.monotonic() + BATCH_TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        if all(
            _process_current_returncode(worker.process) is not None
            for worker in running_workers
        ):
            break
        time.sleep(0.1)

    for worker in running_workers:
        returncode = _process_current_returncode(worker.process)
        if returncode is None:
            kill = getattr(worker.process, "kill", None)
            if callable(kill):
                kill()
            returncode = _process_current_returncode(worker.process)
        worker_results.append(_interrupted_worker_result(worker, returncode=returncode))


def _wait_for_batch_workers(
    workers: list[BatchWorkerProcess],
    worker_results: list[dict[str, object]],
    *,
    duration_seconds: int,
    batch_dir: Path,
    slots: tuple[str, ...],
) -> None:
    running = {worker.slot: worker for worker in workers}
    started_monotonic = time.monotonic()
    next_progress_monotonic = started_monotonic

    while running:
        now_monotonic = time.monotonic()
        if now_monotonic >= next_progress_monotonic:
            _emit_batch_progress(
                started_monotonic=started_monotonic,
                now_monotonic=now_monotonic,
                duration_seconds=duration_seconds,
                batch_dir=batch_dir,
                slots=slots,
                running_slots=set(running),
                worker_results=worker_results,
            )
            next_progress_monotonic = now_monotonic + BATCH_PROGRESS_INTERVAL_SECONDS

        for worker in tuple(running.values()):
            returncode = _worker_returncode(worker)
            if returncode is None:
                continue
            result = _batch_worker_result(worker, returncode)
            worker_results.append(result)
            del running[worker.slot]
            typer.echo(f"{worker.slot} finished with exit code {returncode}.")

        if running:
            time.sleep(min(1.0, BATCH_PROGRESS_INTERVAL_SECONDS))

    _emit_batch_progress(
        started_monotonic=started_monotonic,
        now_monotonic=time.monotonic(),
        duration_seconds=duration_seconds,
        batch_dir=batch_dir,
        slots=slots,
        running_slots=set(),
        worker_results=worker_results,
    )


def _write_final_batch_artifacts(
    artifacts: BatchArtifacts,
    *,
    status: str,
    message: str,
    worker_results: list[dict[str, object]],
) -> dict[str, object]:
    finished_at = utc_now()
    worker_results_tuple = tuple(worker_results)
    device_results = _collect_device_results(artifacts.devices, worker_results_tuple)
    aggregate = _batch_aggregate(device_results)
    if status != "interrupted" and aggregate["verdict"] == "FAIL":
        status = "failed"
    artifacts.write_summary(
        status,
        message=message,
        finished_at=finished_at,
        worker_results=worker_results_tuple,
        device_results=device_results,
        aggregate=aggregate,
    )
    artifacts.write_report(
        status,
        message=message,
        finished_at=finished_at,
        worker_results=worker_results_tuple,
        device_results=device_results,
        aggregate=aggregate,
    )
    return aggregate


def _run_batch(config: BatchConfig) -> int:
    artifacts = create_batch_artifacts(config.output_dir, config=config)
    typer.echo(
        f"Started batch {artifacts.batch_id} with {len(artifacts.devices)} devices."
    )
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _handle_batch_signal)

    workers = []
    worker_results = []
    try:
        for device_artifacts in artifacts.devices:
            try:
                worker = _start_batch_worker(config, device_artifacts)
            except OSError as exc:
                stdout_log = device_artifacts.output_dir / "worker.stdout.log"
                stderr_log = device_artifacts.output_dir / "worker.stderr.log"
                command = _batch_worker_command(config, device_artifacts)
                stdout_log.touch()
                stderr_log.write_text(
                    f"Could not start worker: {exc}\n",
                    encoding="utf-8",
                )
                failure = BatchWorkerStartFailure(
                    slot=device_artifacts.slot,
                    command=command,
                    output_dir=device_artifacts.output_dir,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                    error=str(exc),
                )
                worker_results.append(_batch_worker_start_failure_result(failure))
                typer.echo(f"{device_artifacts.slot} could not start: {exc}")
                continue
            else:
                workers.append(worker)
                typer.echo(f"Started {worker.slot}: {' '.join(worker.command)}")

        _wait_for_batch_workers(
            workers,
            worker_results,
            duration_seconds=config.duration_seconds,
            batch_dir=artifacts.batch_dir,
            slots=tuple(device.slot for device in artifacts.devices),
        )
    except (BatchInterrupted, KeyboardInterrupt):
        _terminate_batch_workers(workers, worker_results)
        completed_workers = sum(
            1 for result in worker_results if result["status"] == "completed"
        )
        message = (
            f"Batch interrupted: {completed_workers}/"
            f"{len(artifacts.devices)} workers completed before shutdown."
        )
        _write_final_batch_artifacts(
            artifacts,
            status="interrupted",
            message=message,
            worker_results=worker_results,
        )
        typer.echo(message, err=True)
        typer.echo(f"Batch artifacts were written under {artifacts.batch_dir}.")
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)

    failed_workers = [result for result in worker_results if result["returncode"] != 0]
    status = "failed" if failed_workers else "completed"
    message = (
        f"Batch completed: {len(worker_results) - len(failed_workers)}/"
        f"{len(worker_results)} workers succeeded."
    )
    aggregate = _write_final_batch_artifacts(
        artifacts,
        status=status,
        message=message,
        worker_results=worker_results,
    )

    typer.echo(message)
    typer.echo(f"Batch artifacts were written under {artifacts.batch_dir}.")
    return 1 if aggregate["verdict"] == "FAIL" else 0


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_run_progress(progress: SerialLogProgress) -> str:
    elapsed = min(progress.elapsed_seconds, float(progress.duration_seconds))
    return (
        f"Progress {progress.percent:5.1f}% "
        f"({_format_elapsed(elapsed)}/{_format_elapsed(progress.duration_seconds)}) "
        f"| lines={progress.lines_read} "
        f"bytes={progress.bytes_read} "
        f"quiet={_format_elapsed(progress.current_silence_seconds)} "
        f"metrics={progress.dev_metrics_count} "
        f"samples={progress.sensor_samples_count} "
        f"alerts={progress.keyword_alerts_count}"
    )


def _make_progress_printer():
    last_length = 0

    def print_progress(progress: SerialLogProgress) -> None:
        nonlocal last_length
        message = _format_run_progress(progress)
        padding = " " * max(0, last_length - len(message))
        typer.echo(f"\r{message}{padding}", err=True, nl=False)
        last_length = len(message)
        if progress.complete:
            typer.echo("", err=True)

    return print_progress


def _resolve_run_port(port: Path | None, auto: bool) -> Path:
    if port is not None and auto:
        raise typer.BadParameter("Use either --port or --auto, not both.")
    if port is not None:
        return port
    if not auto:
        raise typer.BadParameter("Specify --port or --auto.")

    port_infos = list_serial_ports()
    if len(port_infos) == 1:
        return Path(port_infos[0].device)

    if not port_infos:
        typer.secho("No serial ports found. Connect a device or use --port.", err=True)
    else:
        typer.secho(
            "Multiple serial ports found. Choose one with --port:",
            err=True,
        )
        for port_info in port_infos:
            typer.echo(f"  {_format_port_info(port_info)}", err=True)
    raise typer.Exit(code=2)


def _append_check_message(message: str, check_message: str) -> str:
    return f"{message} {check_message}."


def _rule_engine_config(
    tester_config: TesterConfig,
    *,
    expected_sensors: tuple[str, ...],
    expected_metrics: tuple[str, ...],
    finished_at: datetime,
    duration_seconds: int,
) -> RuleEngineConfig:
    return RuleEngineConfig(
        expected_metrics=expected_metrics,
        expected_sensors=expected_sensors,
        sensor_ranges=tester_config.sensor_ranges,
        warn_on_unknown_ranges=tester_config.warn_on_unknown_ranges,
        unknown_non_negative_metrics=tester_config.unknown_non_negative_metrics,
        flatline_window_seconds=tester_config.flatline_window_seconds,
        flatline_fail_after_seconds=tester_config.flatline_fail_after_seconds,
        flatline_min_distinct_values=tester_config.flatline_min_distinct_values,
        cadence_expected_interval_seconds=(
            tester_config.cadence_expected_interval_seconds
        ),
        cadence_warn_after_missed=tester_config.cadence_warn_after_missed,
        cadence_fail_after_missed=tester_config.cadence_fail_after_missed,
        silence_warn_after_seconds=tester_config.silence_warn_after_seconds,
        silence_fail_after_seconds=tester_config.silence_fail_after_seconds,
        connectivity_upload=tester_config.connectivity_upload,
        datalog_upload=tester_config.datalog_upload,
        reference_time=finished_at,
        max_tail_window_seconds=duration_seconds,
        duration_seconds=duration_seconds,
    )


def _report_messages(rule_result: RuleEngineResult) -> tuple[RuleReportMessage, ...]:
    reports = rule_result.reports
    return (
        reports.sensor_presence,
        reports.sensor_flatlines,
        reports.sensor_cadence,
        reports.runtime_counters,
        reports.serial_silence,
        reports.upload_health,
    )


def _append_failed_report_messages(
    message: str,
    reports: tuple[RuleReportMessage, ...],
) -> str:
    for report in reports:
        if report.status == "fail":
            message = _append_check_message(message, report.message)
    return message


def _emit_report_messages(reports: tuple[RuleReportMessage, ...]) -> None:
    for report in reports:
        if report.status == "warn":
            typer.secho(
                f"Warning: {report.message}",
                fg=typer.colors.YELLOW,
                err=True,
            )
        elif report.status == "fail":
            typer.secho(report.message, fg=typer.colors.RED, err=True)


@app.command()
def ports() -> None:
    """List likely USB serial ports detected on the host."""

    _print_ports(list_serial_ports())


@app.command()
def batch(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="TOML batch config describing USB slots and device profiles.",
            exists=False,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=False,
        ),
    ] = None,
    port: Annotated[
        list[Path] | None,
        typer.Option(
            "--port",
            help=(
                "Serial port path for homogeneous explicit batch mode. "
                "Repeat the option for multiple devices."
            ),
            exists=False,
            file_okay=True,
            dir_okay=False,
            writable=False,
            readable=False,
            resolve_path=False,
        ),
    ] = None,
    duration: Annotated[
        str | None,
        typer.Option(
            "--duration",
            help="Batch duration for explicit --port mode.",
        ),
    ] = None,
    baud: Annotated[
        int,
        typer.Option(
            "--baud",
            min=1,
            help="Serial baud rate for explicit --port mode.",
        ),
    ] = 115200,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory where batch artifacts will be written in explicit mode.",
            file_okay=False,
            dir_okay=True,
            resolve_path=False,
        ),
    ] = DEFAULT_OUTPUT_DIR,
    device_config: Annotated[
        Path | None,
        typer.Option(
            "--device-config",
            help="Shared tester profile for explicit homogeneous batch mode.",
            exists=False,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate the batch config and print the planned device runs.",
        ),
    ] = False,
) -> None:
    """Run or preview a USB batch burn-in using one worker per device."""

    batch_config = _load_batch_cli_config(
        config=config,
        ports=port,
        duration=duration,
        baud=baud,
        output_dir=output_dir,
        device_config=device_config,
    )

    if dry_run:
        _print_batch_dry_run(batch_config)
        return

    try:
        exit_code = _run_batch(batch_config)
    except OSError as exc:
        raise typer.BadParameter(
            f"Could not start batch worker: {exc}",
            param_hint="--config",
        ) from exc
    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command()
def run(
    port: Annotated[
        Path | None,
        typer.Option(
            "--port",
            help="Serial port path, for example /dev/ttyACM0.",
            exists=False,
            file_okay=True,
            dir_okay=False,
            writable=False,
            readable=False,
            resolve_path=False,
        ),
    ] = None,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Use the only detected serial port.",
        ),
    ] = False,
    duration: Annotated[
        str,
        typer.Option(
            "--duration",
            help="Test duration, for example 30s, 10m, 2h, or 24h.",
        ),
    ] = ...,
    baud: Annotated[
        int,
        typer.Option(
            "--baud",
            min=1,
            help="Serial baud rate.",
        ),
    ] = 115200,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory where run artifacts will be written.",
            file_okay=False,
            dir_okay=True,
            resolve_path=False,
        ),
    ] = DEFAULT_OUTPUT_DIR,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="TOML tester profile with expected sensors and rule thresholds.",
            exists=False,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=False,
        ),
    ] = None,
    expected_sensor: Annotated[
        list[str] | None,
        typer.Option(
            "--expect-sensor",
            help=(
                "Sensor preset that must produce data. "
                "Repeat the option for multiple sensors."
            ),
        ),
    ] = None,
    expected_metric: Annotated[
        list[str] | None,
        typer.Option(
            "--expect-metric",
            help=(
                "Sensor metric that must appear at least once. "
                "Repeat the option for multiple metrics."
            ),
        ),
    ] = None,
) -> None:
    """Run a USB-C serial burn-in test for one device.

    The command captures raw UART output, parses health observations, evaluates
    all rules, and writes run artifacts under ``--output-dir``. A failed health
    verdict exits with code 1; CLI/config/serial-open failures exit with code 2.
    """

    try:
        duration_seconds = parse_duration_seconds(duration)
    except DurationParseError as exc:
        raise typer.BadParameter(str(exc), param_hint="--duration") from exc

    try:
        resolved_port = _resolve_run_port(port, auto)
    except typer.BadParameter as exc:
        raise typer.BadParameter(str(exc), param_hint="--port") from exc

    try:
        tester_config = load_tester_config(config)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc

    expected_sensors = (*tester_config.expected_sensors, *(expected_sensor or ()))
    expected_metrics = (*tester_config.expected_metrics, *(expected_metric or ()))
    try:
        expected_metrics_for_sensors(expected_sensors)
    except UnknownExpectedSensorError as exc:
        raise typer.BadParameter(str(exc), param_hint="--expect-sensor") from exc

    # Create run artifacts only after static CLI/config validation; from this
    # point on, hardware failures are part of the run record.
    device_identity = detect_device_identity(
        resolved_port,
        port_infos=list_serial_ports(),
    )
    artifacts = create_run_artifacts(
        output_dir,
        port=resolved_port,
        baud=baud,
        duration_input=duration,
        duration_seconds=duration_seconds,
        device_identity=device_identity.as_dict(),
    )
    artifacts.append_event(
        "device_identity_detected",
        **device_identity.as_dict(),
    )

    if not resolved_port.exists():
        message = f"Serial port does not exist: {resolved_port}"
        artifacts.append_event(
            "run_failed", reason="missing_serial_port", message=message
        )
        finished_at = utc_now()
        artifacts.write_summary("failed", message=message, finished_at=finished_at)
        artifacts.write_report("failed", message=message, finished_at=finished_at)
        typer.secho(
            message,
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        with serial.Serial(str(resolved_port), baudrate=baud, timeout=1) as serial_port:
            artifacts.append_event("serial_opened", port=str(resolved_port), baud=baud)
            artifacts.append_event("serial_capture_started")
            stats = capture_raw_serial(
                serial_port,
                artifacts,
                duration_seconds,
                progress_callback=_make_progress_printer(),
            )
    except serial.SerialException as exc:
        message = f"Could not open serial port {resolved_port}: {exc}"
        artifacts.append_event(
            "run_failed", reason="serial_open_failed", message=message
        )
        finished_at = utc_now()
        artifacts.write_summary("failed", message=message, finished_at=finished_at)
        artifacts.write_report("failed", message=message, finished_at=finished_at)
        typer.secho(message, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    finished_at = utc_now()
    serial_log_device_id = (
        stats.serial_device_ids[0] if len(stats.serial_device_ids) == 1 else None
    )
    device_identity = device_identity.with_serial_log_device_id(serial_log_device_id)
    final_device_identity = device_identity.as_dict()
    final_device_identity["serial_log_device_ids"] = list(stats.serial_device_ids)
    artifacts.device_identity = final_device_identity
    artifacts.append_event(
        "device_identity_resolved",
        **final_device_identity,
    )
    rule_result = evaluate_rules(
        stats,
        _rule_engine_config(
            tester_config,
            expected_sensors=expected_sensors,
            expected_metrics=expected_metrics,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
        ),
    )
    sensor_presence = rule_result.reports.sensor_presence
    sensor_ranges = rule_result.reports.sensor_ranges
    sensor_flatlines = rule_result.reports.sensor_flatlines
    sensor_cadence = rule_result.reports.sensor_cadence
    runtime_counters = rule_result.reports.runtime_counters
    serial_silence = rule_result.reports.serial_silence
    upload_health = rule_result.reports.upload_health
    message = (
        f"Captured {stats.lines_read} serial lines "
        f"({stats.bytes_read} bytes) from {resolved_port}."
    )
    report_messages = _report_messages(rule_result)
    message = _append_failed_report_messages(message, report_messages)
    artifacts.append_event(
        "serial_capture_completed",
        lines_read=stats.lines_read,
        bytes_read=stats.bytes_read,
    )
    run_status = "failed" if rule_result.verdict == "FAIL" else "completed"
    if run_status == "failed":
        artifacts.append_event(
            "run_failed",
            reason="sensor_checks",
            failed_checks=list(rule_result.failed_checks),
            message="; ".join(
                finding.message
                for finding in rule_result.findings
                if finding.severity == "fail"
            ),
        )
    else:
        artifacts.append_event("run_completed")
    final_details = {
        "verdict": rule_result.verdict,
        "config": str(config) if config is not None else None,
        "metrics_seen": stats.dev_metrics.seen,
        "samples_seen": stats.sensor_samples_count > 0,
        "findings": [finding.as_dict() for finding in rule_result.findings],
        "serial_lines_read": stats.lines_read,
        "serial_bytes_read": stats.bytes_read,
        "first_serial_line_elapsed_seconds": stats.first_line_elapsed_seconds,
        "last_serial_line_elapsed_seconds": stats.last_line_elapsed_seconds,
        "max_serial_interline_gap_seconds": stats.max_interline_gap_seconds,
        "max_serial_silence_seconds": serial_silence.max_silence_seconds,
        **stats.dev_metrics.as_dict(),
        "keyword_alerts_count": stats.keyword_alerts_count,
        "keyword_alerts": list(stats.keyword_alerts),
        "sensor_samples_count": stats.sensor_samples_count,
        "rules": rule_result.as_dict(),
        "sensor_presence": sensor_presence.as_dict(),
        "sensor_ranges": sensor_ranges.as_dict(),
        "sensor_flatlines": sensor_flatlines.as_dict(),
        "sensor_cadence": sensor_cadence.as_dict(),
        "runtime_counters": runtime_counters.as_dict(),
        "serial_silence": serial_silence.as_dict(),
        "upload_stats": stats.upload_stats.as_dict(),
        "upload_health": upload_health.as_dict(),
        "device_identity": final_device_identity,
    }
    artifacts.write_summary(
        run_status,
        message=message,
        finished_at=finished_at,
        extra=final_details,
    )
    artifacts.write_report(
        run_status,
        message=message,
        finished_at=finished_at,
        details=final_details,
    )
    _emit_report_messages(report_messages)
    if run_status == "failed":
        raise typer.Exit(code=1)
    typer.echo(
        f"Captured serial output for {duration_seconds}s on {resolved_port} at {baud} "
        f"baud. "
        f"Artifacts were written under {artifacts.run_dir}."
    )


if __name__ == "__main__":
    app()
