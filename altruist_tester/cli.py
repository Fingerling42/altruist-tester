"""Command line interface for the Altruist tester."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol

import serial
import typer

from altruist_tester import __version__
from altruist_tester.artifacts import create_run_artifacts, utc_now
from altruist_tester.config import ConfigError, TesterConfig, load_tester_config
from altruist_tester.duration import DurationParseError, parse_duration_seconds
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


class RuleReportMessage(Protocol):
    """Report subset used for CLI status output."""

    status: str
    message: str


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
    if port.vid_pid:
        details.append(f"VID:PID={port.vid_pid}")
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
    artifacts = create_run_artifacts(
        output_dir,
        port=resolved_port,
        baud=baud,
        duration_input=duration,
        duration_seconds=duration_seconds,
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
