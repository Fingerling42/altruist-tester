"""Raw serial logging."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from altruist_tester.artifacts import RunArtifacts
from altruist_tester.identity import parse_identity_from_serial_line
from altruist_tester.parsers.boot_events import BootEvent, parse_boot_event
from altruist_tester.parsers.dev_metrics import DevMetrics, DevMetricsStreamParser
from altruist_tester.parsers.keyword_alerts import KeywordAlert, detect_keyword_alerts
from altruist_tester.parsers.sensor_values import parse_sensor_values
from altruist_tester.parsers.upload_events import (
    UploadEvent,
    UploadStatusStreamParser,
    parse_upload_event,
)
from altruist_tester.samples import SensorSample, SensorSampleRecord, SensorSampleSeries
from altruist_tester.uploads import UploadStats

Number = int | float


class SerialReader(Protocol):
    """Minimal serial reader protocol used by the logger.

    ``serial.Serial`` satisfies this protocol, and tests can provide a fake
    object that returns bytes from ``readline``.
    """

    def readline(self) -> bytes:
        """Read one line from the serial stream."""


@dataclass(frozen=True, slots=True)
class DevMetricsSummary:
    """Aggregate parsed firmware health telemetry for a run.

    The summary keeps min/max values needed by reports while
    ``dev_metrics_records`` keeps the individual snapshots for runtime rules.
    """

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
        """Return whether at least one health telemetry line was parsed."""

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
class BootEventsSummary:
    """Aggregate parsed firmware boot/reset events for a run."""

    count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    last_boot: dict[str, Any] | None = None

    @property
    def seen(self) -> bool:
        """Return whether at least one boot/reset event was parsed."""

        return self.count > 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary."""

        return {
            "boot_events_seen": self.seen,
            "boot_events_count": self.count,
            "first_boot_event_at": self.first_seen,
            "last_boot_event_at": self.last_seen,
            "last_boot_event": self.last_boot,
        }


@dataclass(frozen=True, slots=True)
class SerialLogStats:
    """Summary of one raw serial logging session.

    The object is passed to the rules engine after capture and contains both
    raw serial counters and parsed health observations.
    """

    lines_read: int
    bytes_read: int
    first_line_elapsed_seconds: float | None = None
    last_line_elapsed_seconds: float | None = None
    max_interline_gap_seconds: float | None = None
    dev_metrics: DevMetricsSummary = field(default_factory=DevMetricsSummary)
    dev_metrics_records: tuple[dict[str, object], ...] = ()
    boot_events: BootEventsSummary = field(default_factory=BootEventsSummary)
    boot_event_records: tuple[dict[str, object], ...] = ()
    keyword_alerts_count: int = 0
    keyword_alerts: tuple[dict[str, str], ...] = ()
    sensor_samples_count: int = 0
    sensor_series: SensorSampleSeries = field(default_factory=SensorSampleSeries)
    upload_stats: UploadStats = field(default_factory=UploadStats)
    serial_device_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SerialLogProgress:
    """Point-in-time progress for one raw serial logging session.

    Instances are emitted to the optional progress callback while capture is
    running and once more with ``complete=True`` at the end.
    """

    elapsed_seconds: float
    duration_seconds: int
    current_silence_seconds: float
    lines_read: int
    bytes_read: int
    dev_metrics_count: int
    keyword_alerts_count: int
    sensor_samples_count: int
    complete: bool = False

    @property
    def percent(self) -> float:
        """Return elapsed percentage capped to 100."""

        if self.duration_seconds <= 0:
            return 100.0
        return min(100.0, (self.elapsed_seconds / self.duration_seconds) * 100.0)


def _decode_serial_line(line: bytes) -> str:
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def _min_optional(current: Number | None, value: Number | None) -> Number | None:
    if value is None:
        return current
    if current is None:
        return value
    return min(current, value)


def _max_optional(current: Number | None, value: Number | None) -> Number | None:
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


def _update_boot_events_summary(
    summary: BootEventsSummary,
    boot_event: BootEvent,
    event_ts: str,
) -> BootEventsSummary:
    payload = boot_event.as_event_payload()
    return BootEventsSummary(
        count=summary.count + 1,
        first_seen=summary.first_seen or event_ts,
        last_seen=event_ts,
        last_boot=payload,
    )


def _append_keyword_alerts(
    artifacts: RunArtifacts,
    alerts: list[KeywordAlert],
) -> tuple[dict[str, str], ...]:
    appended_alerts = []
    for alert in alerts:
        payload = alert.as_event_payload()
        artifacts.append_event("keyword_alert", **payload)
        appended_alerts.append(payload)
    return tuple(appended_alerts)


def _append_boot_event(
    artifacts: RunArtifacts,
    boot_event: BootEvent,
    records: list[dict[str, object]],
    summary: BootEventsSummary,
) -> BootEventsSummary:
    payload = boot_event.as_event_payload()
    event = artifacts.append_event("boot_event", **payload)
    records.append({"ts": event["ts"], **payload})
    return _update_boot_events_summary(summary, boot_event, event["ts"])


def _append_sensor_samples(
    artifacts: RunArtifacts,
    samples: list[SensorSample],
    series: SensorSampleSeries,
) -> tuple[SensorSampleRecord, ...]:
    appended_samples = []
    for sample in samples:
        record = artifacts.append_sample(sample)
        series.append(record)
        appended_samples.append(record)
    return tuple(appended_samples)


def _append_dev_metrics(
    artifacts: RunArtifacts,
    metrics: DevMetrics,
    records: list[dict[str, object]],
    summary: DevMetricsSummary,
) -> DevMetricsSummary:
    payload = metrics.as_event_payload()
    event = artifacts.append_event("dev_metrics", **payload)
    records.append({"ts": event["ts"], **payload})
    return _update_dev_metrics_summary(summary, metrics, event["ts"])


def _append_upload_event(
    artifacts: RunArtifacts,
    upload_event: UploadEvent,
    upload_stats: UploadStats,
) -> None:
    payload = upload_event.as_event_payload()
    artifacts.append_event("upload_event", **payload)
    upload_stats.append(upload_event)


def _build_progress(
    *,
    now: float,
    started_at: float,
    duration_seconds: int,
    last_line_elapsed_seconds: float | None,
    lines_read: int,
    bytes_read: int,
    metrics_summary: DevMetricsSummary,
    keyword_alerts_count: int,
    sensor_series: SensorSampleSeries,
    complete: bool = False,
) -> SerialLogProgress:
    elapsed_seconds = max(0.0, now - started_at)
    if last_line_elapsed_seconds is None:
        current_silence_seconds = elapsed_seconds
    else:
        current_silence_seconds = max(0.0, elapsed_seconds - last_line_elapsed_seconds)

    return SerialLogProgress(
        elapsed_seconds=elapsed_seconds,
        duration_seconds=duration_seconds,
        current_silence_seconds=current_silence_seconds,
        lines_read=lines_read,
        bytes_read=bytes_read,
        dev_metrics_count=metrics_summary.count,
        keyword_alerts_count=keyword_alerts_count,
        sensor_samples_count=sensor_series.count(),
        complete=complete,
    )


def capture_raw_serial(
    serial_port: SerialReader,
    artifacts: RunArtifacts,
    duration_seconds: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    mirror_lines_to_events: bool = False,
    progress_callback: Callable[[SerialLogProgress], None] | None = None,
    progress_interval_seconds: float = 1.0,
) -> SerialLogStats:
    """Capture raw serial output until the requested duration elapses.

    Every received byte is appended to ``serial.log``. Decoded lines are also
    parsed for keyword alerts, health telemetry, and sensor samples;
    those structured observations are written to ``events.jsonl`` or
    ``samples.jsonl`` as appropriate.

    :param serial_port: Object with a ``readline`` method returning bytes.
    :param artifacts: Run artifact paths and writers.
    :param duration_seconds: Capture duration in seconds.
    :param clock: Monotonic clock function, injectable for tests.
    :param mirror_lines_to_events: Also mirror decoded serial lines to
        ``events.jsonl`` when raw line-level events are needed.
    :param progress_callback: Optional receiver for live progress updates.
    :param progress_interval_seconds: Minimum interval between progress
        updates.
    :returns: Aggregated counters and parsed observations for rule evaluation.
    """

    started_at = clock()
    deadline = started_at + duration_seconds
    next_progress_at = started_at
    lines_read = 0
    bytes_read = 0
    first_line_elapsed_seconds: float | None = None
    last_line_elapsed_seconds: float | None = None
    max_interline_gap_seconds: float | None = None
    metrics_parser = DevMetricsStreamParser()
    upload_status_parser = UploadStatusStreamParser()
    metrics_summary = DevMetricsSummary()
    metrics_records: list[dict[str, object]] = []
    boot_events_summary = BootEventsSummary()
    boot_event_records: list[dict[str, object]] = []
    keyword_alerts: list[dict[str, str]] = []
    sensor_series = SensorSampleSeries()
    upload_stats = UploadStats()
    serial_device_ids: list[str] = []

    def emit_progress(now: float, *, complete: bool = False) -> None:
        if progress_callback is None:
            return
        progress_callback(
            _build_progress(
                now=now,
                started_at=started_at,
                duration_seconds=duration_seconds,
                last_line_elapsed_seconds=last_line_elapsed_seconds,
                lines_read=lines_read,
                bytes_read=bytes_read,
                metrics_summary=metrics_summary,
                keyword_alerts_count=len(keyword_alerts),
                sensor_series=sensor_series,
                complete=complete,
            )
        )

    with artifacts.serial_log.open("ab") as raw_log:
        while (now := clock()) < deadline:
            line = serial_port.readline()
            now = clock()
            if not line:
                if now >= next_progress_at:
                    emit_progress(now)
                    next_progress_at = now + progress_interval_seconds
                continue

            raw_log.write(line)
            raw_log.flush()
            lines_read += 1
            bytes_read += len(line)
            line_elapsed_seconds = max(0.0, now - started_at)
            if first_line_elapsed_seconds is None:
                first_line_elapsed_seconds = line_elapsed_seconds
            if last_line_elapsed_seconds is not None:
                gap_seconds = max(0.0, line_elapsed_seconds - last_line_elapsed_seconds)
                max_interline_gap_seconds = _max_optional(
                    max_interline_gap_seconds,
                    gap_seconds,
                )
            last_line_elapsed_seconds = line_elapsed_seconds
            decoded_line = _decode_serial_line(line)
            if mirror_lines_to_events:
                artifacts.append_event("serial_line", line=decoded_line)

            boot_event = parse_boot_event(decoded_line)
            if boot_event is not None:
                boot_events_summary = _append_boot_event(
                    artifacts,
                    boot_event,
                    boot_event_records,
                    boot_events_summary,
                )

            serial_device_id = parse_identity_from_serial_line(decoded_line)
            if (
                serial_device_id is not None
                and serial_device_id not in serial_device_ids
            ):
                serial_device_ids.append(serial_device_id)
                artifacts.append_event(
                    "device_identity_observed",
                    source="serial_log",
                    device_id=serial_device_id,
                )

            keyword_alerts.extend(
                _append_keyword_alerts(
                    artifacts,
                    detect_keyword_alerts(decoded_line),
                )
            )
            _append_sensor_samples(
                artifacts,
                parse_sensor_values(decoded_line),
                sensor_series,
            )
            upload_event = parse_upload_event(decoded_line)
            if upload_event is not None:
                _append_upload_event(artifacts, upload_event, upload_stats)
            upload_status_parser.record_explicit_event(upload_event)
            for status_event in upload_status_parser.feed(decoded_line):
                _append_upload_event(artifacts, status_event, upload_stats)

            metrics = metrics_parser.feed(decoded_line)
            if metrics is not None:
                metrics_summary = _append_dev_metrics(
                    artifacts,
                    metrics,
                    metrics_records,
                    metrics_summary,
                )

            if now >= next_progress_at:
                emit_progress(now)
                next_progress_at = now + progress_interval_seconds

    emit_progress(clock(), complete=True)

    return SerialLogStats(
        lines_read=lines_read,
        bytes_read=bytes_read,
        first_line_elapsed_seconds=first_line_elapsed_seconds,
        last_line_elapsed_seconds=last_line_elapsed_seconds,
        max_interline_gap_seconds=max_interline_gap_seconds,
        dev_metrics=metrics_summary,
        dev_metrics_records=tuple(metrics_records),
        boot_events=boot_events_summary,
        boot_event_records=tuple(boot_event_records),
        keyword_alerts_count=len(keyword_alerts),
        keyword_alerts=tuple(keyword_alerts),
        sensor_samples_count=sensor_series.count(),
        sensor_series=sensor_series,
        upload_stats=upload_stats,
        serial_device_ids=tuple(serial_device_ids),
    )
