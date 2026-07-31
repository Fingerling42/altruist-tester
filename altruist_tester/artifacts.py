"""Run artifact helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from altruist_tester.config import BatchConfig, BatchDeviceConfig
from altruist_tester.samples import SensorSample, SensorSampleRecord

ARTIFACT_FILENAMES = ("serial.log", "events.jsonl", "samples.jsonl")
_RUN_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    """Format a timestamp for JSON and JSONL artifacts.

    The output uses millisecond precision and the ``Z`` suffix so all run
    files share one compact UTC timestamp format.
    """

    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_run_id_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H-%M-%SZ")


def device_hint_from_port(port: Path) -> str:
    """Build a filesystem-safe device hint from a serial port path."""

    hint = port.name or "unknown"
    hint = _RUN_ID_SAFE_RE.sub("-", hint).strip("-_.")
    return hint or "unknown"


def safe_artifact_name(value: str) -> str:
    """Return a filesystem-safe name for an artifact directory segment."""

    name = _RUN_ID_SAFE_RE.sub("-", value).strip("-_.")
    return name or "unknown"


def _format_finding_line(finding: dict[str, Any]) -> str:
    severity = str(finding.get("severity", "warn")).upper()
    code = str(finding.get("code", "FINDING"))
    message = str(finding.get("message", ""))
    return f"- [{severity}] {code}: {message}"


def _format_last_dev_metrics(metrics: dict[str, Any]) -> str:
    if not metrics:
        return "none"

    errors = metrics.get("errors")
    error_text = ""
    if isinstance(errors, dict):
        error_text = (
            f", errors wifi={errors.get('wifi')} "
            f"sensor={errors.get('sensor')} sd={errors.get('sd')}"
        )

    return (
        f"status={metrics.get('status')}, uptime={metrics.get('uptime_sec')}s, "
        f"boot={metrics.get('boot')}, wifi={metrics.get('wifi_state')}, "
        f"rssi={metrics.get('rssi')}, tx={metrics.get('tx')}{error_text}"
    )


def _format_boot_context(context: dict[str, Any]) -> str:
    if not context:
        return "none"

    return (
        f"reset_reason={context.get('reset_reason') or 'unknown'}, "
        f"reset_code={context.get('reset_code')}, "
        f"boot={context.get('boot')}, "
        f"crash_valid={context.get('crash_valid')}, "
        f"prev_uptime={context.get('prev_uptime_sec')}s, "
        f"prev_heap={context.get('prev_free_heap')}, "
        f"last_section={context.get('last_section') or 'unknown'}"
    )


def _dev_metrics_boot_context(metrics: dict[str, Any]) -> dict[str, Any]:
    if not metrics or metrics.get("reset_reason") is None:
        return {}

    return {
        "reset_reason": metrics.get("reset_reason"),
        "reset_code": metrics.get("reset_code"),
        "boot": metrics.get("boot"),
        "crash_valid": metrics.get("crash_valid"),
        "prev_uptime_sec": metrics.get("prev_uptime_sec"),
        "prev_free_heap": metrics.get("prev_free_heap"),
        "last_section": metrics.get("last_section"),
    }


def _append_final_report_details(lines: list[str], details: dict[str, Any]) -> None:
    identity = details.get("device_identity")
    if isinstance(identity, dict):
        lines.extend(
            [
                "",
                "Device:",
                f"- id: {identity.get('device_id') or 'unknown'}",
                f"- mac: {identity.get('mac') or 'unknown'}",
                f"- usb serial: {identity.get('usb_serial') or 'unknown'}",
                f"- by-id: {identity.get('by_id') or 'unknown'}",
                f"- by-path: {identity.get('by_path') or 'unknown'}",
            ]
        )
        conflicts = identity.get("conflicts")
        if isinstance(conflicts, list) and conflicts:
            lines.append("- identity conflicts: yes")

    last_metrics_text = _format_last_dev_metrics(details.get("last_dev_metrics") or {})
    last_metrics = details.get("last_dev_metrics") or {}
    boot_reset = details.get("boot_reset") or {}
    if isinstance(boot_reset, dict):
        last_boot = boot_reset.get("last_boot_event")
        last_boot_context = last_boot if isinstance(last_boot, dict) else {}
        health_boot_context = (
            _dev_metrics_boot_context(last_metrics)
            if isinstance(last_metrics, dict)
            else {}
        )
        lines.extend(
            [
                "",
                "Boot/reset:",
                f"- boot events seen: {boot_reset.get('boot_events_count', 0)}",
                f"- last boot event: {_format_boot_context(last_boot_context)}",
                "- last health reset context: "
                f"{_format_boot_context(health_boot_context)}",
            ]
        )

    rules = details.get("rules")
    if isinstance(rules, dict):
        lines.extend(
            [
                "",
                "Verdict:",
                f"- verdict: {rules.get('verdict')}",
                f"- status: {rules.get('status')}",
            ]
        )

        findings = rules.get("findings")
        if isinstance(findings, list) and findings:
            lines.extend(["", "Findings:"])
            # Keep the text report readable; summary.json keeps the full list.
            for finding in findings[:10]:
                if isinstance(finding, dict):
                    lines.append(_format_finding_line(finding))
            if len(findings) > 10:
                lines.append(f"- ... {len(findings) - 10} more findings")
        else:
            lines.extend(["", "Findings:", "- none"])

    lines.extend(
        [
            "",
            "Health:",
            f"- config: {details.get('config') or 'built-in defaults'}",
            f"- dev metrics seen: {details.get('metrics_seen')}",
            f"- samples seen: {details.get('samples_seen')}",
            f"- serial lines: {details.get('serial_lines_read')}",
            f"- serial bytes: {details.get('serial_bytes_read')}",
            f"- max serial silence: {details.get('max_serial_silence_seconds')}s",
            f"- last dev metrics: {last_metrics_text}",
        ]
    )

    sensor_presence = details.get("sensor_presence")
    sensor_ranges = details.get("sensor_ranges")
    sensor_flatlines = details.get("sensor_flatlines")
    sensor_cadence = details.get("sensor_cadence")
    subsystem_health = details.get("subsystem_health")
    upload_health = details.get("upload_health")
    if isinstance(sensor_presence, dict):
        observed = sensor_presence.get("observed_metrics") or []
        missing = sensor_presence.get("missing_metrics") or []
        lines.extend(
            [
                "",
                "Sensors:",
                f"- samples: {details.get('sensor_samples_count')}",
                f"- observed metrics: {', '.join(observed) if observed else 'none'}",
                f"- missing metrics: {', '.join(missing) if missing else 'none'}",
            ]
        )
    if isinstance(sensor_ranges, dict):
        lines.append(
            "- ranges: "
            f"{sensor_ranges.get('status')} "
            f"({sensor_ranges.get('checked_samples_count')} samples, "
            f"{sensor_ranges.get('failure_count')} failures, "
            f"{sensor_ranges.get('warning_count')} warnings)"
        )
    if isinstance(sensor_flatlines, dict):
        lines.append(
            "- flatlines: "
            f"{sensor_flatlines.get('status')} "
            f"({sensor_flatlines.get('failure_count')} failures, "
            f"{sensor_flatlines.get('warning_count')} warnings)"
        )
    if isinstance(sensor_cadence, dict):
        lines.append(
            "- cadence: "
            f"{sensor_cadence.get('status')} "
            f"({sensor_cadence.get('failure_count')} failures, "
            f"{sensor_cadence.get('warning_count')} warnings)"
        )
    if isinstance(subsystem_health, dict):
        lines.extend(
            [
                "",
                "Subsystems:",
                "- health: "
                f"{subsystem_health.get('status')} "
                f"({subsystem_health.get('events_count')} events, "
                f"{subsystem_health.get('failure_count')} failures, "
                f"{subsystem_health.get('warning_count')} warnings)",
            ]
        )
    if isinstance(upload_health, dict):
        lines.extend(["", "Uploads:"])
        channels = upload_health.get("channels")
        if isinstance(channels, dict):
            for channel_name in sorted(channels):
                channel = channels[channel_name]
                if not isinstance(channel, dict):
                    continue
                success_rate = channel.get("success_rate")
                if isinstance(success_rate, float):
                    success_rate_text = f"{success_rate:.1%}"
                else:
                    success_rate_text = "n/a"
                lines.append(
                    f"- {channel_name}: {channel.get('status')} "
                    f"(mode={channel.get('mode')}, "
                    f"attempts={channel.get('attempts')}, "
                    f"successes={channel.get('successes')}, "
                    f"failures={channel.get('failures')}, "
                    f"success_rate={success_rate_text})"
                )


@dataclass(slots=True)
class RunArtifacts:
    """Paths and metadata for one test run.

    Instances know where every run artifact lives and provide the only write
    helpers used by the logger and CLI. This keeps JSONL formatting and report
    layout consistent across success and failure paths.
    """

    run_id: str
    run_dir: Path
    started_at: datetime
    port: Path
    baud: int
    duration_input: str
    duration_seconds: int
    device_identity: dict[str, Any] | None = None
    serial_log: Path = field(init=False)
    events_jsonl: Path = field(init=False)
    samples_jsonl: Path = field(init=False)
    summary_json: Path = field(init=False)
    report_txt: Path = field(init=False)

    def __post_init__(self) -> None:
        self.serial_log = self.run_dir / "serial.log"
        self.events_jsonl = self.run_dir / "events.jsonl"
        self.samples_jsonl = self.run_dir / "samples.jsonl"
        self.summary_json = self.run_dir / "summary.json"
        self.report_txt = self.run_dir / "report.txt"

    def append_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """Append one structured runtime event to ``events.jsonl``.

        :param event_type: Stable event type name.
        :param payload: JSON-serializable event fields.
        :returns: The exact event object written to disk, including its
            timestamp.
        """

        event = {
            "ts": format_timestamp(utc_now()),
            "type": event_type,
            **payload,
        }
        with self.events_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def append_sample(self, sample: SensorSample) -> SensorSampleRecord:
        """Append one timestamped sensor sample to ``samples.jsonl``.

        :returns: The stored sample record, including the generated timestamp.
        """

        record = {
            "ts": format_timestamp(utc_now()),
            **sample.as_payload(),
        }
        with self.samples_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return SensorSampleRecord.from_mapping(record)

    def write_summary(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write the current machine-readable run summary.

        ``summary.json`` is rewritten as the run progresses. The final write is
        the authoritative machine-readable result for automation and CI-style
        consumers.
        """

        summary: dict[str, Any] = {
            "status": status,
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "started_at": format_timestamp(self.started_at),
            "port": str(self.port),
            "baud": self.baud,
            "duration": self.duration_input,
            "duration_sec": self.duration_seconds,
            "artifacts": {
                "serial_log": str(self.serial_log),
                "events_jsonl": str(self.events_jsonl),
                "samples_jsonl": str(self.samples_jsonl),
                "summary_json": str(self.summary_json),
                "report_txt": str(self.report_txt),
            },
        }
        if self.device_identity is not None:
            summary["device_identity"] = self.device_identity
        if finished_at is not None:
            summary["finished_at"] = format_timestamp(finished_at)
        if message is not None:
            summary["message"] = message
        if extra is not None:
            summary.update(extra)

        self.summary_json.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_report(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write a human-readable run report.

        ``report.txt`` is intentionally compact and review-oriented. Detailed
        per-rule payloads stay in ``summary.json``.
        """

        lines = [
            "Altruist Tester Run Report",
            "===========================",
            "",
            f"Run ID: {self.run_id}",
            f"Status: {status}",
            f"Started at: {format_timestamp(self.started_at)}",
        ]
        if finished_at is not None:
            lines.append(f"Finished at: {format_timestamp(finished_at)}")
        lines.extend(
            [
                f"Port: {self.port}",
                f"Baud: {self.baud}",
                f"Duration: {self.duration_input} ({self.duration_seconds}s)",
            ]
        )
        if message is not None:
            lines.extend(["", message])
        if details is not None:
            _append_final_report_details(lines, details)
        lines.extend(
            [
                "",
                "Artifacts:",
                f"- serial log: {self.serial_log}",
                f"- events: {self.events_jsonl}",
                f"- samples: {self.samples_jsonl}",
                f"- summary: {self.summary_json}",
            ]
        )

        self.report_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class BatchDeviceArtifacts:
    """Paths reserved for one device inside a batch run directory."""

    slot: str
    slot_dir_name: str
    device: BatchDeviceConfig
    output_dir: Path


@dataclass(frozen=True, slots=True)
class BatchArtifacts:
    """Paths and metadata for one USB batch run."""

    batch_id: str
    batch_dir: Path
    started_at: datetime
    config: BatchConfig
    summary_json: Path
    report_txt: Path
    devices_dir: Path
    devices: tuple[BatchDeviceArtifacts, ...]

    def write_summary(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
        worker_results: tuple[dict[str, Any], ...] = (),
        device_results: tuple[dict[str, Any], ...] = (),
        aggregate: dict[str, Any] | None = None,
    ) -> None:
        """Write the current machine-readable batch summary."""

        _write_batch_summary(
            self,
            status,
            message=message,
            finished_at=finished_at,
            worker_results=worker_results,
            device_results=device_results,
            aggregate=aggregate,
        )

    def write_report(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
        worker_results: tuple[dict[str, Any], ...] = (),
        device_results: tuple[dict[str, Any], ...] = (),
        aggregate: dict[str, Any] | None = None,
    ) -> None:
        """Write the current human-readable batch report."""

        _write_batch_report(
            self,
            status,
            message=message,
            finished_at=finished_at,
            worker_results=worker_results,
            device_results=device_results,
            aggregate=aggregate,
        )


def _batch_device_summary(device_artifacts: BatchDeviceArtifacts) -> dict[str, Any]:
    device = device_artifacts.device
    return {
        "slot": device.slot,
        "slot_dir": str(device_artifacts.output_dir),
        "port": str(device.port),
        "model": device.model,
        "config": str(device.effective_config) if device.effective_config else None,
        "expected_sensors": list(device.expected_sensors),
        "expected_metrics": list(device.expected_metrics),
    }


def _write_batch_summary(
    artifacts: BatchArtifacts,
    status: str,
    *,
    message: str | None = None,
    finished_at: datetime | None = None,
    worker_results: tuple[dict[str, Any], ...] = (),
    device_results: tuple[dict[str, Any], ...] = (),
    aggregate: dict[str, Any] | None = None,
) -> None:
    summary = {
        "status": status,
        "batch_id": artifacts.batch_id,
        "batch_dir": str(artifacts.batch_dir),
        "started_at": format_timestamp(artifacts.started_at),
        "duration": artifacts.config.duration_input,
        "duration_sec": artifacts.config.duration_seconds,
        "baud": artifacts.config.baud,
        "output_dir": str(artifacts.config.output_dir),
        "device_config": (
            str(artifacts.config.device_config)
            if artifacts.config.device_config is not None
            else None
        ),
        "artifacts": {
            "batch_summary_json": str(artifacts.summary_json),
            "batch_report_txt": str(artifacts.report_txt),
            "devices_dir": str(artifacts.devices_dir),
        },
        "devices": [
            _batch_device_summary(device_artifacts)
            for device_artifacts in artifacts.devices
        ],
    }
    if finished_at is not None:
        summary["finished_at"] = format_timestamp(finished_at)
    if message is not None:
        summary["message"] = message
    if worker_results:
        summary["workers"] = list(worker_results)
    if device_results:
        summary["device_results"] = list(device_results)
    if aggregate is not None:
        summary.update(aggregate)
    artifacts.summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _batch_report_device_line(device: dict[str, Any]) -> str:
    identity = device.get("device_id") or device.get("mac") or "unknown-device"
    verdict = device.get("verdict") or "unknown"
    findings_count = device.get("findings_count")
    findings_text = (
        f"{findings_count} findings" if findings_count is not None else "n/a"
    )
    return (
        f"- {device.get('slot')} {identity} {verdict} "
        f"({device.get('model') or 'unspecified'}, {findings_text})"
    )


def _append_batch_report_device_details(
    lines: list[str],
    device: dict[str, Any],
) -> None:
    lines.extend(
        [
            f"  port: {device.get('port')}",
            f"  config: {device.get('config')}",
            f"  run dir: {device.get('run_dir') or 'unknown'}",
            f"  report: {device.get('report_txt') or 'unknown'}",
        ]
    )
    usb_serial = device.get("usb_serial")
    by_id = device.get("by_id")
    by_path = device.get("by_path")
    identity_values = (
        device.get("device_id"),
        device.get("mac"),
        usb_serial,
        by_id,
        by_path,
    )
    if not any(identity_values):
        lines.append("  identity warning: identity was not resolved")
    else:
        if usb_serial:
            lines.append(f"  usb serial: {usb_serial}")
        if by_id:
            lines.append(f"  by-id: {by_id}")
        if by_path:
            lines.append(f"  by-path: {by_path}")
    conflicts = device.get("identity_conflicts")
    if isinstance(conflicts, list) and conflicts:
        lines.append("  identity warning: conflicting identity sources")
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            source = conflict.get("source") or "unknown"
            device_id = conflict.get("device_id") or "unknown"
            lines.append(f"    - {source}: {device_id}")
    failed_checks = device.get("failed_checks")
    if isinstance(failed_checks, list) and failed_checks:
        checks = ", ".join(str(check) for check in failed_checks)
        lines.append(f"  failed checks: {checks}")
    finding_messages = device.get("finding_messages")
    if isinstance(finding_messages, list) and finding_messages:
        lines.append("  findings:")
        for message in finding_messages:
            lines.append(f"    - {message}")
    summary_error = device.get("summary_error")
    if summary_error:
        lines.append(f"  summary error: {summary_error}")


def _write_batch_report(
    artifacts: BatchArtifacts,
    status: str,
    *,
    message: str | None = None,
    finished_at: datetime | None = None,
    worker_results: tuple[dict[str, Any], ...] = (),
    device_results: tuple[dict[str, Any], ...] = (),
    aggregate: dict[str, Any] | None = None,
) -> None:
    lines = [
        "Altruist Tester Batch Report",
        "============================",
        "",
        f"Batch ID: {artifacts.batch_id}",
        f"Status: {status}",
        f"Started at: {format_timestamp(artifacts.started_at)}",
    ]
    if finished_at is not None:
        lines.append(f"Finished at: {format_timestamp(finished_at)}")
    lines.extend(
        [
            f"Duration: {artifacts.config.duration_input} "
            f"({artifacts.config.duration_seconds}s)",
            f"Baud: {artifacts.config.baud}",
            f"Output dir: {artifacts.config.output_dir}",
        ]
    )
    if message is not None:
        lines.extend(["", message])
    if aggregate is not None:
        lines.extend(
            [
                "",
                f"Verdict: {aggregate.get('verdict')}",
                "Devices: "
                f"{aggregate.get('devices_total')} total, "
                f"{aggregate.get('devices_passed')} pass, "
                f"{aggregate.get('devices_warned')} warn, "
                f"{aggregate.get('devices_failed')} fail",
            ]
        )
    lines.extend(["", "Devices:"])
    if aggregate is not None and isinstance(aggregate.get("devices"), list):
        for device in aggregate["devices"]:
            if not isinstance(device, dict):
                continue
            lines.append(_batch_report_device_line(device))
            _append_batch_report_device_details(lines, device)
    else:
        for device_artifacts in artifacts.devices:
            device = device_artifacts.device
            lines.extend(
                [
                    f"- {device.slot}",
                    f"  model: {device.model or 'unspecified'}",
                    f"  port: {device.port}",
                    f"  config: {device.effective_config}",
                    f"  output dir: {device_artifacts.output_dir}",
                ]
            )
    if worker_results:
        lines.extend(["", "Workers:"])
        for result in worker_results:
            lines.extend(
                [
                    f"- {result.get('slot')}: {result.get('status')}",
                    f"  return code: {result.get('returncode')}",
                    f"  stdout: {result.get('stdout_log')}",
                    f"  stderr: {result.get('stderr_log')}",
                ]
            )
    lines.extend(
        [
            "",
            "Artifacts:",
            f"- batch summary: {artifacts.summary_json}",
            f"- batch report: {artifacts.report_txt}",
            f"- devices: {artifacts.devices_dir}",
        ]
    )
    artifacts.report_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_batch_artifacts(
    output_dir: Path,
    *,
    config: BatchConfig,
    started_at: datetime | None = None,
) -> BatchArtifacts:
    """Create the artifact directory skeleton for one USB batch run.

    This initializes only batch-level files and per-slot output directories.
    The usual per-device run files are created later by each device worker
    inside its reserved slot directory.
    """

    started_at = started_at or utc_now()
    batch_id = f"batch_{_format_run_id_time(started_at)}"
    batch_dir = output_dir / batch_id
    suffix = 1
    while batch_dir.exists():
        suffix += 1
        batch_dir = output_dir / f"{batch_id}-{suffix}"

    devices_dir = batch_dir / "devices"
    devices_dir.mkdir(parents=True)
    device_artifacts = tuple(
        BatchDeviceArtifacts(
            slot=device.slot,
            slot_dir_name=safe_artifact_name(device.slot),
            device=device,
            output_dir=devices_dir / safe_artifact_name(device.slot),
        )
        for device in config.devices
    )
    for device in device_artifacts:
        device.output_dir.mkdir()

    artifacts = BatchArtifacts(
        batch_id=batch_dir.name,
        batch_dir=batch_dir,
        started_at=started_at,
        config=config,
        summary_json=batch_dir / "batch_summary.json",
        report_txt=batch_dir / "batch_report.txt",
        devices_dir=devices_dir,
        devices=device_artifacts,
    )
    artifacts.write_summary("initialized")
    artifacts.write_report("initialized")
    return artifacts


def create_run_artifacts(
    output_dir: Path,
    *,
    port: Path,
    baud: int,
    duration_input: str,
    duration_seconds: int,
    device_identity: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> RunArtifacts:
    """Create and initialize the artifact directory for one run.

    Creates ``serial.log``, ``events.jsonl``, ``samples.jsonl``,
    ``summary.json``, and ``report.txt``. If the timestamp/port-derived run ID
    already exists, a numeric suffix is appended.
    """

    started_at = started_at or utc_now()
    run_id = f"{_format_run_id_time(started_at)}_{device_hint_from_port(port)}"
    run_dir = output_dir / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = output_dir / f"{run_id}-{suffix}"

    run_dir.mkdir(parents=True)
    artifacts = RunArtifacts(
        run_id=run_dir.name,
        run_dir=run_dir,
        started_at=started_at,
        port=port,
        baud=baud,
        duration_input=duration_input,
        duration_seconds=duration_seconds,
        device_identity=device_identity,
    )

    for filename in ARTIFACT_FILENAMES:
        (run_dir / filename).touch()
    artifacts.write_summary("running")
    artifacts.write_report("running", message="Run artifacts were initialized.")
    # events.jsonl is a runtime audit trail; final rule payloads are written to
    # summary.json/report.txt at the end of the run.
    artifacts.append_event(
        "run_started",
        port=str(port),
        baud=baud,
        duration=duration_input,
        duration_sec=duration_seconds,
        device_identity=device_identity,
    )
    return artifacts
