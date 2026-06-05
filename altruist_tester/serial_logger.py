"""Raw serial logging."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from altruist_tester.artifacts import RunArtifacts
from altruist_tester.parsers.dev_metrics import DevMetrics, DevMetricsStreamParser


class SerialReader(Protocol):
    """Minimal serial reader protocol used by the logger."""

    def readline(self) -> bytes:
        """Read one line from the serial stream."""


@dataclass(frozen=True, slots=True)
class DevMetricsSummary:
    """Aggregate parsed development metrics for a run."""

    count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    last_metrics: dict[str, object] | None = None
    max_boot: int | None = None
    min_uptime_sec: int | None = None
    max_uptime_sec: int | None = None
    min_rssi: int | None = None
    max_rssi: int | None = None
    min_esp_temp_c: float | None = None
    max_esp_temp_c: float | None = None
    max_errors: dict[str, int | None] = field(
        default_factory=lambda: {"wifi": None, "sensor": None, "sd": None}
    )

    @property
    def seen(self) -> bool:
        """Return whether at least one metrics block was parsed."""

        return self.count > 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary."""

        return {
            "dev_metrics_seen": self.seen,
            "dev_metrics_count": self.count,
            "first_dev_metrics_at": self.first_seen,
            "last_dev_metrics_at": self.last_seen,
            "last_dev_metrics": self.last_metrics,
            "max_boot": self.max_boot,
            "min_uptime_sec": self.min_uptime_sec,
            "max_uptime_sec": self.max_uptime_sec,
            "min_rssi": self.min_rssi,
            "max_rssi": self.max_rssi,
            "min_esp_temp_c": self.min_esp_temp_c,
            "max_esp_temp_c": self.max_esp_temp_c,
            "max_errors": self.max_errors,
        }


@dataclass(frozen=True, slots=True)
class SerialLogStats:
    """Summary of one raw serial logging session."""

    lines_read: int
    bytes_read: int
    dev_metrics: DevMetricsSummary = field(default_factory=DevMetricsSummary)


def _decode_serial_line(line: bytes) -> str:
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def _min_optional(current: int | float | None, value: int | float | None):
    if value is None:
        return current
    if current is None:
        return value
    return min(current, value)


def _max_optional(current: int | float | None, value: int | float | None):
    if value is None:
        return current
    if current is None:
        return value
    return max(current, value)


def _max_error_counters(
    current: dict[str, int | None],
    metrics: DevMetrics,
) -> dict[str, int | None]:
    errors = metrics.errors.as_dict()
    return {
        name: _max_optional(current.get(name), errors.get(name))
        for name in ("wifi", "sensor", "sd")
    }


def _update_dev_metrics_summary(
    summary: DevMetricsSummary,
    metrics: DevMetrics,
    event_ts: str,
) -> DevMetricsSummary:
    payload = metrics.as_event_payload()
    return DevMetricsSummary(
        count=summary.count + 1,
        first_seen=summary.first_seen or event_ts,
        last_seen=event_ts,
        last_metrics=payload,
        max_boot=_max_optional(summary.max_boot, metrics.boot),
        min_uptime_sec=_min_optional(summary.min_uptime_sec, metrics.uptime_sec),
        max_uptime_sec=_max_optional(summary.max_uptime_sec, metrics.uptime_sec),
        min_rssi=_min_optional(summary.min_rssi, metrics.rssi),
        max_rssi=_max_optional(summary.max_rssi, metrics.rssi),
        min_esp_temp_c=_min_optional(summary.min_esp_temp_c, metrics.esp_temp_c),
        max_esp_temp_c=_max_optional(summary.max_esp_temp_c, metrics.esp_temp_c),
        max_errors=_max_error_counters(summary.max_errors, metrics),
    )


def capture_raw_serial(
    serial_port: SerialReader,
    artifacts: RunArtifacts,
    duration_seconds: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    mirror_lines_to_events: bool = False,
) -> SerialLogStats:
    """Capture raw serial output until the requested duration elapses."""

    deadline = clock() + duration_seconds
    lines_read = 0
    bytes_read = 0
    metrics_parser = DevMetricsStreamParser()
    metrics_summary = DevMetricsSummary()

    with artifacts.serial_log.open("ab") as raw_log:
        while clock() < deadline:
            line = serial_port.readline()
            if not line:
                continue

            raw_log.write(line)
            raw_log.flush()
            lines_read += 1
            bytes_read += len(line)
            decoded_line = _decode_serial_line(line)
            if mirror_lines_to_events:
                artifacts.append_event("serial_line", line=decoded_line)

            metrics = metrics_parser.feed(decoded_line)
            if metrics is not None:
                event = artifacts.append_event(
                    "dev_metrics", **metrics.as_event_payload()
                )
                metrics_summary = _update_dev_metrics_summary(
                    metrics_summary,
                    metrics,
                    event["ts"],
                )

    trailing_metrics = metrics_parser.finish()
    if trailing_metrics is not None:
        event = artifacts.append_event(
            "dev_metrics", **trailing_metrics.as_event_payload()
        )
        metrics_summary = _update_dev_metrics_summary(
            metrics_summary,
            trailing_metrics,
            event["ts"],
        )

    return SerialLogStats(
        lines_read=lines_read,
        bytes_read=bytes_read,
        dev_metrics=metrics_summary,
    )
