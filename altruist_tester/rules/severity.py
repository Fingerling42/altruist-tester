"""Severity policy shared by health rules."""

from __future__ import annotations

from typing import Literal

RuleSeverity = Literal["warn", "fail"]
UploadMode = Literal["disabled", "optional", "required"]

_SUBSYSTEM_OVERRIDES: dict[tuple[str, str, str], RuleSeverity] = {
    ("event", "wifi", "sta_recovery"): "warn",
    ("event", "wifi", "sta_recovery_reboot"): "fail",
    ("error", "display", "epd_stuck"): "warn",
    ("error", "display", "busy_timeout"): "warn",
}


def severity_for_keyword_alert(_code: str) -> RuleSeverity:
    """Return severity for runtime crash/reset keyword alerts."""

    return "fail"


def severity_for_missing_sensor_metrics() -> RuleSeverity:
    """Return severity for absent mandatory sensor data."""

    return "fail"


def severity_for_missing_health_telemetry() -> RuleSeverity:
    """Return severity for absent firmware health telemetry."""

    return "fail"


def severity_for_missing_boot_context() -> RuleSeverity:
    """Return severity for absent reset/build context logs."""

    return "warn"


def severity_for_subsystem_event(
    *,
    level: str,
    subsystem: str,
    reason: str,
) -> RuleSeverity:
    """Return severity for one structured firmware subsystem event."""

    event_key = (level.lower(), subsystem.lower(), reason.lower())
    if override := _SUBSYSTEM_OVERRIDES.get(event_key):
        return override
    if event_key[0] == "error":
        if event_key[1] == "display":
            return "warn"
        return "fail"
    return "warn"


def severity_for_upload_mode(mode: UploadMode) -> RuleSeverity:
    """Return finding severity for upload checks in one configured mode."""

    return "fail" if mode == "required" else "warn"
