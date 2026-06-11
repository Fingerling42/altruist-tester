"""Sensor flatline checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from altruist_tester.rules.presence import canonical_metric_name
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries

FlatlineStatus = Literal["ok", "warn", "fail"]

DEFAULT_FLATLINE_WINDOW_SECONDS = 30 * 60
DEFAULT_FLATLINE_FAIL_AFTER_SECONDS = 60 * 60
DEFAULT_MIN_DISTINCT_VALUES = 2
ZERO_FLATLINE_WARN_ONLY_METRICS = frozenset(
    {
        "co",
        "epa_aqi",
        "fast_aqi",
        "no2",
        "o3",
        "pm1",
        "pm10",
        "pm25",
        "radiation",
    }
)


@dataclass(frozen=True, slots=True)
class SensorFlatlineFinding:
    """Result for one sensor metric time series."""

    status: FlatlineStatus
    sensor: str
    metric: str
    canonical_metric: str
    samples_count: int
    distinct_values_count: int
    elapsed_seconds: float
    message: str

    @property
    def key(self) -> tuple[str, str]:
        """Return the original sensor metric key."""

        return (self.sensor, self.metric)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "sensor": self.sensor,
            "metric": self.metric,
            "canonical_metric": self.canonical_metric,
            "samples_count": self.samples_count,
            "distinct_values_count": self.distinct_values_count,
            "elapsed_seconds": self.elapsed_seconds,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SensorFlatlineReport:
    """Aggregate flatline check result for all sensor metric series."""

    status: FlatlineStatus
    checked_series_count: int
    warning_count: int
    failure_count: int
    findings: tuple[SensorFlatlineFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the flatline check did not fail."""

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


def _elapsed_seconds(records: list[SensorSampleRecord]) -> float:
    timestamps = [
        parsed
        for record in records
        if (parsed := _parse_timestamp(record.ts)) is not None
    ]
    if len(timestamps) < 2:
        return 0.0
    return max(0.0, (max(timestamps) - min(timestamps)).total_seconds())


def _distinct_values(records: Iterable[SensorSampleRecord]) -> set[float]:
    return {record.value for record in records}


def _is_warn_only_zero_flatline(
    distinct_values: set[float],
    canonical_metric: str,
) -> bool:
    return (
        distinct_values == {0.0} and canonical_metric in ZERO_FLATLINE_WARN_ONLY_METRICS
    )


def check_series_flatline(
    sensor: str,
    metric: str,
    records: list[SensorSampleRecord],
    *,
    flatline_window_seconds: int = DEFAULT_FLATLINE_WINDOW_SECONDS,
    flatline_fail_after_seconds: int = DEFAULT_FLATLINE_FAIL_AFTER_SECONDS,
    min_distinct_values: int = DEFAULT_MIN_DISTINCT_VALUES,
) -> SensorFlatlineFinding:
    """Check one sensor metric series for stuck values."""

    samples_count = len(records)
    distinct_values = _distinct_values(records)
    distinct_values_count = len(distinct_values)
    elapsed_seconds = _elapsed_seconds(records)
    canonical_metric = canonical_metric_name(metric)

    if samples_count < min_distinct_values:
        return SensorFlatlineFinding(
            status="warn",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=samples_count,
            distinct_values_count=distinct_values_count,
            elapsed_seconds=elapsed_seconds,
            message=f"{sensor}/{metric} has not enough samples for flatline check",
        )

    if distinct_values_count >= min_distinct_values:
        return SensorFlatlineFinding(
            status="ok",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=samples_count,
            distinct_values_count=distinct_values_count,
            elapsed_seconds=elapsed_seconds,
            message=f"{sensor}/{metric} changed during the run",
        )

    if (
        elapsed_seconds >= flatline_fail_after_seconds
        and not _is_warn_only_zero_flatline(distinct_values, canonical_metric)
    ):
        return SensorFlatlineFinding(
            status="fail",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=samples_count,
            distinct_values_count=distinct_values_count,
            elapsed_seconds=elapsed_seconds,
            message=f"{sensor}/{metric} was flat for {elapsed_seconds:g}s",
        )

    if elapsed_seconds >= flatline_window_seconds:
        return SensorFlatlineFinding(
            status="warn",
            sensor=sensor,
            metric=metric,
            canonical_metric=canonical_metric,
            samples_count=samples_count,
            distinct_values_count=distinct_values_count,
            elapsed_seconds=elapsed_seconds,
            message=f"{sensor}/{metric} did not change for {elapsed_seconds:g}s",
        )

    return SensorFlatlineFinding(
        status="warn",
        sensor=sensor,
        metric=metric,
        canonical_metric=canonical_metric,
        samples_count=samples_count,
        distinct_values_count=distinct_values_count,
        elapsed_seconds=elapsed_seconds,
        message=f"{sensor}/{metric} has limited variation so far",
    )


def check_sensor_flatlines(
    series: SensorSampleSeries,
    *,
    flatline_window_seconds: int = DEFAULT_FLATLINE_WINDOW_SECONDS,
    flatline_fail_after_seconds: int = DEFAULT_FLATLINE_FAIL_AFTER_SECONDS,
    min_distinct_values: int = DEFAULT_MIN_DISTINCT_VALUES,
) -> SensorFlatlineReport:
    """Check all sensor metric series for stuck values."""

    findings = tuple(
        check_series_flatline(
            sensor,
            metric,
            records,
            flatline_window_seconds=flatline_window_seconds,
            flatline_fail_after_seconds=flatline_fail_after_seconds,
            min_distinct_values=min_distinct_values,
        )
        for (sensor, metric), records in sorted(series.by_key.items())
    )
    warning_count = sum(1 for finding in findings if finding.status == "warn")
    failure_count = sum(1 for finding in findings if finding.status == "fail")

    if failure_count:
        status: FlatlineStatus = "fail"
        message = f"{failure_count} sensor metric series failed flatline checks"
    elif warning_count:
        status = "warn"
        message = f"{warning_count} sensor metric series need more variation"
    else:
        status = "ok"
        message = "All sensor metric series changed enough"

    return SensorFlatlineReport(
        status=status,
        checked_series_count=len(findings),
        warning_count=warning_count,
        failure_count=failure_count,
        findings=findings,
        message=message,
    )
