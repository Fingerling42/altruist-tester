"""Checks that firmware release logs are sufficient for tester rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from altruist_tester.parsers.upload_events import UploadChannel
from altruist_tester.rules.severity import (
    RuleSeverity,
    severity_for_missing_boot_context,
    severity_for_missing_health_telemetry,
    severity_for_missing_sensor_metrics,
    severity_for_upload_mode,
)
from altruist_tester.rules.uploads import UploadChannelConfig
from altruist_tester.serial_logger import SerialLogStats

LogContractStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class LogContractFinding:
    """One missing release-log signal finding."""

    status: RuleSeverity
    code: str
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class LogContractReport:
    """Report describing whether logs are rich enough for acceptance testing."""

    status: LogContractStatus
    startup_window_seconds: int
    signals: dict[str, bool]
    findings: tuple[LogContractFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when log sufficiency did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "startup_window_seconds": self.startup_window_seconds,
            "signals": self.signals,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _has_health_reset_context(stats: SerialLogStats) -> bool:
    last_metrics = stats.dev_metrics.last_metrics
    if not isinstance(last_metrics, dict):
        if not stats.dev_metrics_records:
            return False
        last_metrics = stats.dev_metrics_records[-1]
    return bool(last_metrics.get("reset_reason") or last_metrics.get("reset_code"))


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_dev_metrics_at(stats: SerialLogStats) -> str | None:
    if stats.dev_metrics.first_seen:
        return stats.dev_metrics.first_seen
    if stats.dev_metrics_records:
        value = stats.dev_metrics_records[0].get("ts")
        return str(value) if value else None
    return None


def _first_sensor_sample_at(stats: SerialLogStats) -> str | None:
    timestamps = [
        record.ts
        for records in stats.sensor_series.by_key.values()
        for record in records[:1]
    ]
    return min(timestamps) if timestamps else None


def _within_startup_window(
    *,
    capture_started_at: str | None,
    observed_at: str | None,
    startup_window_seconds: int,
) -> bool | None:
    started_at = _parse_timestamp(capture_started_at)
    seen_at = _parse_timestamp(observed_at)
    if started_at is None or seen_at is None:
        return None
    return seen_at <= started_at + timedelta(seconds=startup_window_seconds)


def _has_health_telemetry(stats: SerialLogStats) -> bool:
    return stats.dev_metrics.seen or bool(stats.dev_metrics_records)


def _has_sensor_samples(stats: SerialLogStats) -> bool:
    return stats.sensor_samples_count > 0 or stats.sensor_series.count() > 0


def _has_firmware_build_info(stats: SerialLogStats) -> bool:
    return stats.build_events.seen or bool(stats.build_event_records)


def _upload_observed(stats: SerialLogStats, channel: UploadChannel) -> bool:
    return stats.upload_stats.channel(channel).observed


def _missing_upload_finding(
    *,
    channel: UploadChannel,
    config: UploadChannelConfig,
) -> LogContractFinding | None:
    if config.mode == "disabled":
        return None
    return LogContractFinding(
        status=severity_for_upload_mode(config.mode),
        code=f"LOG_CONTRACT_{channel.upper()}_UPLOAD_MISSING",
        message=(
            f"{channel} upload telemetry was not observed; release logs are not "
            "showing configured upload activity"
        ),
    )


def _late_signal_finding(
    *,
    signal: str,
    status: RuleSeverity,
    startup_window_seconds: int,
) -> LogContractFinding:
    return LogContractFinding(
        status=status,
        code=f"LOG_CONTRACT_{signal.upper()}_LATE",
        message=(
            f"{signal} telemetry was not observed during the first "
            f"{startup_window_seconds} seconds"
        ),
    )


def check_log_contract(
    stats: SerialLogStats,
    *,
    startup_window_seconds: int,
    connectivity_upload: UploadChannelConfig,
    datalog_upload: UploadChannelConfig,
) -> LogContractReport:
    """Check that release logs expose the minimum tester-facing telemetry."""

    signals = {
        "health_telemetry": _has_health_telemetry(stats),
        "sensor_samples": _has_sensor_samples(stats),
        "firmware_build_info": _has_firmware_build_info(stats),
        "boot_or_reset_context": stats.boot_events.seen
        or _has_health_reset_context(stats),
        "connectivity_upload": _upload_observed(stats, "connectivity"),
        "datalog_upload": _upload_observed(stats, "datalog"),
    }

    findings: list[LogContractFinding] = []
    if not signals["health_telemetry"]:
        findings.append(
            LogContractFinding(
                status=severity_for_missing_health_telemetry(),
                code="LOG_CONTRACT_HEALTH_MISSING",
                message="[HEALTH] telemetry was not observed",
            )
        )
    if not signals["sensor_samples"]:
        findings.append(
            LogContractFinding(
                status=severity_for_missing_sensor_metrics(),
                code="LOG_CONTRACT_SENSOR_SAMPLES_MISSING",
                message="Sensor payload samples were not observed",
            )
        )
    if not signals["boot_or_reset_context"]:
        findings.append(
            LogContractFinding(
                status=severity_for_missing_boot_context(),
                code="LOG_CONTRACT_BOOT_CONTEXT_MISSING",
                message="[BOOT] or [HEALTH] reset context was not observed",
            )
        )
    if not signals["firmware_build_info"]:
        findings.append(
            LogContractFinding(
                status=severity_for_missing_boot_context(),
                code="LOG_CONTRACT_BUILD_INFO_MISSING",
                message="[BUILD] firmware identity was not observed",
            )
        )

    health_in_window = _within_startup_window(
        capture_started_at=stats.capture_started_at,
        observed_at=_first_dev_metrics_at(stats),
        startup_window_seconds=startup_window_seconds,
    )
    samples_in_window = _within_startup_window(
        capture_started_at=stats.capture_started_at,
        observed_at=_first_sensor_sample_at(stats),
        startup_window_seconds=startup_window_seconds,
    )
    boot_in_window = _within_startup_window(
        capture_started_at=stats.capture_started_at,
        observed_at=stats.boot_events.first_seen,
        startup_window_seconds=startup_window_seconds,
    )
    build_in_window = _within_startup_window(
        capture_started_at=stats.capture_started_at,
        observed_at=stats.build_events.first_seen,
        startup_window_seconds=startup_window_seconds,
    )
    if signals["health_telemetry"] and health_in_window is False:
        findings.append(
            _late_signal_finding(
                signal="health",
                status=severity_for_missing_health_telemetry(),
                startup_window_seconds=startup_window_seconds,
            )
        )
    if signals["sensor_samples"] and samples_in_window is False:
        findings.append(
            _late_signal_finding(
                signal="sensor_samples",
                status=severity_for_missing_sensor_metrics(),
                startup_window_seconds=startup_window_seconds,
            )
        )
    if stats.boot_events.seen and boot_in_window is False:
        findings.append(
            _late_signal_finding(
                signal="boot_context",
                status=severity_for_missing_boot_context(),
                startup_window_seconds=startup_window_seconds,
            )
        )
    if signals["firmware_build_info"] and build_in_window is False:
        findings.append(
            _late_signal_finding(
                signal="firmware_build_info",
                status=severity_for_missing_boot_context(),
                startup_window_seconds=startup_window_seconds,
            )
        )

    signals["health_telemetry_in_startup_window"] = health_in_window is not False
    signals["sensor_samples_in_startup_window"] = samples_in_window is not False
    signals["boot_context_in_startup_window"] = boot_in_window is not False
    signals["firmware_build_info_in_startup_window"] = build_in_window is not False

    if not signals["connectivity_upload"] and (
        finding := _missing_upload_finding(
            channel="connectivity",
            config=connectivity_upload,
        )
    ):
        findings.append(finding)
    if not signals["datalog_upload"] and (
        finding := _missing_upload_finding(
            channel="datalog",
            config=datalog_upload,
        )
    ):
        findings.append(finding)

    if any(finding.status == "fail" for finding in findings):
        status: LogContractStatus = "fail"
    elif findings:
        status = "warn"
    else:
        status = "ok"

    if status == "ok":
        message = "Release logs contain the minimum tester-facing telemetry"
    else:
        missing = ", ".join(finding.code for finding in findings)
        message = "Release logs are missing tester-facing telemetry: " + missing

    return LogContractReport(
        status=status,
        startup_window_seconds=startup_window_seconds,
        signals=signals,
        findings=tuple(findings),
        message=message,
    )
