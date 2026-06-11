"""Sensor update cadence checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from altruist_tester.rules.presence import canonical_metric_name
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries

CadenceStatus = Literal["ok", "warn", "fail"]

DEFAULT_EXPECTED_INTERVAL_SECONDS = 5 * 60
DEFAULT_WARN_AFTER_MISSED = 2
DEFAULT_FAIL_AFTER_MISSED = 4


@dataclass(frozen=True, slots=True)
class SensorCadenceFinding:
    """Result for one sensor metric update cadence check."""

    status: CadenceStatus
    sensor: str
    metric: str
    canonical_metric: str
    samples_count: int
    max_gap_seconds: float | None
    tail_gap_seconds: float | None
    expected_interval_seconds: int
    warn_after_seconds: int
    fail_after_seconds: int
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "sensor": self.sensor,
            "metric": self.metric,
            "canonical_metric": self.canonical_metric,
            "samples_count": self.samples_count,
            "max_gap_seconds": self.max_gap_seconds,
            "tail_gap_seconds": self.tail_gap_seconds,
            "expected_interval_seconds": self.expected_interval_seconds,
            "warn_after_seconds": self.warn_after_seconds,
            "fail_after_seconds": self.fail_after_seconds,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SensorCadenceReport:
    """Aggregate update cadence result for all sensor metric series."""

    status: CadenceStatus
    checked_series_count: int
    warning_count: int
    failure_count: int
    findings: tuple[SensorCadenceFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the cadence check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "checked_series_count": self.checked_series_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_timestamps(records: list[SensorSampleRecord]) -> tuple[datetime, ...]:
    return tuple(
        sorted(
            parsed
            for record in records
            if (parsed := _parse_timestamp(record.ts)) is not None
        )
    )


def _effective_reference_time(
    timestamps: tuple[datetime, ...],
    reference_time: datetime | None,
    max_tail_window_seconds: int | None,
) -> datetime | None:
    if reference_time is None or not timestamps:
        return reference_time
    if max_tail_window_seconds is None:
        return reference_time

    latest_sample_time = timestamps[-1]
    max_reference_time = latest_sample_time + timedelta(seconds=max_tail_window_seconds)
    return min(reference_time, max_reference_time)


def _max_gap_seconds(
    timestamps: tuple[datetime, ...],
    reference_time: datetime | None,
) -> tuple[float | None, float | None]:
    gaps = [
        (current - previous).total_seconds()
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ]
    tail_gap = None
    if reference_time is not None and timestamps:
        tail_gap = max(0.0, (reference_time - timestamps[-1]).total_seconds())
        gaps.append(tail_gap)

    if not gaps:
        return None, tail_gap
    return max(gaps), tail_gap


def check_series_cadence(
    sensor: str,
    metric: str,
    records: list[SensorSampleRecord],
    *,
    reference_time: datetime | None = None,
    max_tail_window_seconds: int | None = None,
    expected_interval_seconds: int = DEFAULT_EXPECTED_INTERVAL_SECONDS,
    warn_after_missed: int = DEFAULT_WARN_AFTER_MISSED,
    fail_after_missed: int = DEFAULT_FAIL_AFTER_MISSED,
) -> SensorCadenceFinding:
    """Check one sensor metric series update cadence."""

    timestamps = _record_timestamps(records)
    effective_reference_time = _effective_reference_time(
        timestamps,
        reference_time,
        max_tail_window_seconds,
    )
    max_gap, tail_gap = _max_gap_seconds(timestamps, effective_reference_time)
    canonical_metric = canonical_metric_name(metric)
    warn_after_seconds = expected_interval_seconds * warn_after_missed
    fail_after_seconds = expected_interval_seconds * fail_after_missed

    if not timestamps:
        return SensorCadenceFinding(
            status="warn",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=0,
            max_gap_seconds=None,
            tail_gap_seconds=None,
            expected_interval_seconds=expected_interval_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
            message=f"{sensor}/{metric} has no timestamped samples",
        )

    if max_gap is None:
        return SensorCadenceFinding(
            status="warn",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=len(timestamps),
            max_gap_seconds=None,
            tail_gap_seconds=tail_gap,
            expected_interval_seconds=expected_interval_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
            message=f"{sensor}/{metric} has not enough samples for cadence check",
        )

    if max_gap >= fail_after_seconds:
        return SensorCadenceFinding(
            status="fail",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=len(timestamps),
            max_gap_seconds=max_gap,
            tail_gap_seconds=tail_gap,
            expected_interval_seconds=expected_interval_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
            message=f"{sensor}/{metric} update gap reached {max_gap:g}s",
        )

    if max_gap >= warn_after_seconds:
        return SensorCadenceFinding(
            status="warn",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=len(timestamps),
            max_gap_seconds=max_gap,
            tail_gap_seconds=tail_gap,
            expected_interval_seconds=expected_interval_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
            message=f"{sensor}/{metric} update gap reached {max_gap:g}s",
        )

    return SensorCadenceFinding(
        status="ok",
        sensor=sensor,
        metric=metric,
        canonical_metric=canonical_metric,
        samples_count=len(timestamps),
        max_gap_seconds=max_gap,
        tail_gap_seconds=tail_gap,
        expected_interval_seconds=expected_interval_seconds,
        warn_after_seconds=warn_after_seconds,
        fail_after_seconds=fail_after_seconds,
        message=f"{sensor}/{metric} update cadence is regular",
    )


def check_sensor_cadence(
    series: SensorSampleSeries,
    *,
    reference_time: datetime | None = None,
    max_tail_window_seconds: int | None = None,
    expected_interval_seconds: int = DEFAULT_EXPECTED_INTERVAL_SECONDS,
    warn_after_missed: int = DEFAULT_WARN_AFTER_MISSED,
    fail_after_missed: int = DEFAULT_FAIL_AFTER_MISSED,
) -> SensorCadenceReport:
    """Check all sensor metric series update cadence."""

    findings = tuple(
        check_series_cadence(
            sensor,
            metric,
            records,
            reference_time=reference_time,
            max_tail_window_seconds=max_tail_window_seconds,
            expected_interval_seconds=expected_interval_seconds,
            warn_after_missed=warn_after_missed,
            fail_after_missed=fail_after_missed,
        )
        for (sensor, metric), records in sorted(series.by_key.items())
    )
    warning_count = sum(1 for finding in findings if finding.status == "warn")
    failure_count = sum(1 for finding in findings if finding.status == "fail")

    if failure_count:
        status: CadenceStatus = "fail"
        message = f"{failure_count} sensor metric series missed too many updates"
    elif warning_count:
        status = "warn"
        message = f"{warning_count} sensor metric series have delayed updates"
    else:
        status = "ok"
        message = "All sensor metric series update regularly"

    return SensorCadenceReport(
        status=status,
        checked_series_count=len(findings),
        warning_count=warning_count,
        failure_count=failure_count,
        findings=findings,
        message=message,
    )
