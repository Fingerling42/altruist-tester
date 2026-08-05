"""Urban particulate matter plausibility checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from altruist_tester.rules.presence import canonical_metric_name
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries

UrbanPmStatus = Literal["ok", "warn"]

URBAN_PM_METRICS = frozenset({"pm10", "pm25"})
DEFAULT_MIN_SAMPLES = 100
DEFAULT_ZERO_RATIO_WARN = 0.9
DEFAULT_NEAR_ZERO_MAX = 1.0


@dataclass(frozen=True, slots=True)
class UrbanPmFinding:
    """Suspicious nearly-zero particulate matter series for Urban devices."""

    status: UrbanPmStatus
    sensor: str
    metric: str
    canonical_metric: str
    samples_count: int
    zero_count: int
    zero_ratio: float
    max_value: float
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "sensor": self.sensor,
            "metric": self.metric,
            "canonical_metric": self.canonical_metric,
            "samples_count": self.samples_count,
            "zero_count": self.zero_count,
            "zero_ratio": self.zero_ratio,
            "max_value": self.max_value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class UrbanPmReport:
    """Aggregate nearly-zero particulate matter check result."""

    status: UrbanPmStatus
    checked_series_count: int
    warning_count: int
    findings: tuple[UrbanPmFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when no nearly-zero PM warnings were found."""

        return self.status == "ok"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "checked_series_count": self.checked_series_count,
            "warning_count": self.warning_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _is_zero(value: float) -> bool:
    return value == 0.0


def _check_series(
    sensor: str,
    metric: str,
    records: list[SensorSampleRecord],
    *,
    min_samples: int,
    zero_ratio_warn: float,
    near_zero_max: float,
) -> UrbanPmFinding | None:
    samples_count = len(records)
    if samples_count < min_samples:
        return None

    values = [record.value for record in records]
    zero_count = sum(1 for value in values if _is_zero(value))
    zero_ratio = zero_count / samples_count
    max_value = max(values)
    canonical_metric = canonical_metric_name(metric)

    if zero_ratio < zero_ratio_warn or max_value > near_zero_max:
        return None

    return UrbanPmFinding(
        status="warn",
        sensor=sensor,
        metric=metric,
        canonical_metric=canonical_metric,
        samples_count=samples_count,
        zero_count=zero_count,
        zero_ratio=zero_ratio,
        max_value=max_value,
        message=(
            f"{sensor}/{metric} is nearly zero "
            f"({zero_count}/{samples_count} zeros, max={max_value:g})"
        ),
    )


def check_urban_pm_nearly_zero(
    series: SensorSampleSeries,
    *,
    enabled: bool,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    zero_ratio_warn: float = DEFAULT_ZERO_RATIO_WARN,
    near_zero_max: float = DEFAULT_NEAR_ZERO_MAX,
) -> UrbanPmReport:
    """Warn when Urban particulate matter readings stay almost zero.

    A few low PM readings are valid. This rule targets long Urban/SDS series
    where almost every sample is exactly zero and the observed maximum stays
    near zero, which usually deserves a manual sensor/airflow check.
    """

    if not enabled:
        return UrbanPmReport(
            status="ok",
            checked_series_count=0,
            warning_count=0,
            findings=(),
            message="Urban PM nearly-zero check is not enabled",
        )

    checked_series_count = 0
    findings: list[UrbanPmFinding] = []
    for (sensor, metric), records in sorted(series.by_key.items()):
        canonical_metric = canonical_metric_name(metric)
        if canonical_metric not in URBAN_PM_METRICS:
            continue

        checked_series_count += 1
        finding = _check_series(
            sensor,
            metric,
            records,
            min_samples=min_samples,
            zero_ratio_warn=zero_ratio_warn,
            near_zero_max=near_zero_max,
        )
        if finding is not None:
            findings.append(finding)

    if findings:
        status: UrbanPmStatus = "warn"
        message = f"{len(findings)} Urban PM series are nearly zero"
    else:
        status = "ok"
        message = "Urban PM series are not nearly zero"

    return UrbanPmReport(
        status=status,
        checked_series_count=checked_series_count,
        warning_count=len(findings),
        findings=tuple(findings),
        message=message,
    )
