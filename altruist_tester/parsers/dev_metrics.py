"""Parser for firmware health telemetry."""

from __future__ import annotations

import re
from dataclasses import dataclass

from altruist_tester.parsers.boot_events import parse_key_value_fields

_HEALTH_RE = re.compile(
    r"^\[HEALTH\]\s+"
    r"uptime=(?P<uptime>\d+)\s+"
    r"boot=(?P<boot>\d+)\s+"
    r"heap=(?P<heap>\d+)\s+"
    r"rssi=(?P<rssi>-?\d+)\s+"
    r"tx=(?P<tx>\d+)\s+"
    r"errors=(?P<errors>\d+)\s+"
    r"wifi=(?P<wifi>[01])\s+"
    r"wifi_errors=(?P<wifi_errors>\d+)\s+"
    r"sensor_errors=(?P<sensor_errors>\d+)\s+"
    r"sd_errors=(?P<sd_errors>\d+)"
    r"(?:\s+(?P<extra_fields>.*))?\s*$"
)


@dataclass(frozen=True, slots=True)
class DevMetricsErrors:
    """Error counters from one firmware health telemetry line."""

    wifi: int | None = None
    sensor: int | None = None
    sd: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        """Return counters as a JSON-friendly mapping."""

        return {
            "wifi": self.wifi,
            "sensor": self.sensor,
            "sd": self.sd,
        }


@dataclass(frozen=True, slots=True)
class DevMetrics:
    """Parsed firmware health telemetry.

    Some field names retain the historical ``dev_metrics`` terminology because
    they are part of existing artifact keys.
    """

    status: str | None = None
    uptime_sec: int | None = None
    boot: int | None = None
    wifi_state: str | None = None
    rssi: int | None = None
    tx: int | None = None
    errors: DevMetricsErrors = DevMetricsErrors()
    free_heap: int | None = None
    error_count: int | None = None
    reset_reason: str | None = None
    reset_code: int | None = None
    crash_valid: bool | None = None
    prev_uptime_sec: int | None = None
    prev_free_heap: int | None = None
    last_section_id: int | None = None
    last_section: str | None = None

    def as_event_payload(self) -> dict[str, object]:
        """Return metrics as an event payload."""

        return {
            "status": self.status,
            "uptime_sec": self.uptime_sec,
            "boot": self.boot,
            "wifi_state": self.wifi_state,
            "rssi": self.rssi,
            "tx": self.tx,
            "errors": self.errors.as_dict(),
            "free_heap": self.free_heap,
            "error_count": self.error_count,
            "reset_reason": self.reset_reason,
            "reset_code": self.reset_code,
            "crash_valid": self.crash_valid,
            "prev_uptime_sec": self.prev_uptime_sec,
            "prev_free_heap": self.prev_free_heap,
            "last_section_id": self.last_section_id,
            "last_section": self.last_section,
        }


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value in {"0", "false", "False"}:
        return False
    if value in {"1", "true", "True"}:
        return True
    return None


def parse_health_line(line: str) -> DevMetrics | None:
    """Parse one HEALTH line; return None for incomplete or unrelated input."""

    match = _HEALTH_RE.match(line.strip())
    if match is None:
        return None

    extra_fields = parse_key_value_fields(match.group("extra_fields") or "")
    rssi = int(match.group("rssi"))
    error_count = int(match.group("errors"))
    errors = DevMetricsErrors(
        wifi=int(match.group("wifi_errors")),
        sensor=int(match.group("sensor_errors")),
        sd=int(match.group("sd_errors")),
    )
    return DevMetrics(
        status="ERROR" if error_count else "ALIVE",
        uptime_sec=int(match.group("uptime")),
        boot=int(match.group("boot")),
        wifi_state="DISCONNECTED" if match.group("wifi") == "0" else "OK",
        rssi=rssi,
        tx=int(match.group("tx")),
        errors=errors,
        free_heap=int(match.group("heap")),
        error_count=error_count,
        reset_reason=extra_fields.get("reset_reason"),
        reset_code=_optional_int(extra_fields.get("reset_code")),
        crash_valid=_optional_bool(extra_fields.get("crash_valid")),
        prev_uptime_sec=_optional_int(extra_fields.get("prev_uptime")),
        prev_free_heap=_optional_int(extra_fields.get("prev_heap")),
        last_section_id=_optional_int(extra_fields.get("last_section_id")),
        last_section=extra_fields.get("last_section"),
    )
