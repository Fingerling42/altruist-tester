"""Default sensor value ranges for basic sanity checks."""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from altruist_tester.samples import SensorSample, SensorSampleRecord

RangeStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class SensorRange:
    """Allowed numeric range for one logical sensor metric."""

    name: str
    minimum: float | None = None
    maximum: float | None = None
    canonical_unit: str | None = None
    non_negative: bool = False

    def contains(self, value: float) -> bool:
        """Return True when the value is inside this range."""

        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True


@dataclass(frozen=True, slots=True)
class SensorRangeCheck:
    """Result of checking one sensor value against default ranges."""

    status: RangeStatus
    metric: str
    value: float | None
    rule: str | None
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the value did not fail the range check."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly check result."""

        return {
            "status": self.status,
            "metric": self.metric,
            "value": self.value,
            "rule": self.rule,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SensorRangeReport:
    """Aggregate range sanity result for parsed sensor samples."""

    status: RangeStatus
    checked_samples_count: int
    warning_count: int
    failure_count: int
    findings: tuple[SensorRangeCheck, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the range check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "checked_samples_count": self.checked_samples_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


DEFAULT_SENSOR_RANGES: dict[str, SensorRange] = {
    "temperature": SensorRange("temperature_c", minimum=-40.0, maximum=85.0),
    "humidity": SensorRange("humidity_percent", minimum=0.0, maximum=100.0),
    "pressure": SensorRange("pressure_hpa", minimum=850.0, maximum=1100.0),
    "P1": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "P2": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "pm1": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "pm10": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "pm25": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "pm2_5": SensorRange("pm_ug_m3", minimum=0.0, maximum=2000.0),
    "co2": SensorRange("co2_ppm", minimum=350.0, maximum=10000.0),
    "noiseAvg": SensorRange("noise_dba", minimum=20.0, maximum=130.0),
    "noiseMax": SensorRange("noise_dba", minimum=20.0, maximum=130.0),
    "radiation": SensorRange("radiation", minimum=0.0, non_negative=True),
    "co": SensorRange("gas_metrics", minimum=0.0, non_negative=True),
    "o3": SensorRange("gas_metrics", minimum=0.0, non_negative=True),
    "no2": SensorRange("gas_metrics", minimum=0.0, non_negative=True),
    "fast_aqi": SensorRange("gas_metrics", minimum=0.0, non_negative=True),
    "epa_aqi": SensorRange("gas_metrics", minimum=0.0, non_negative=True),
}

DEFAULT_NON_NEGATIVE_METRICS = frozenset(
    {
        "aqi",
        "gas",
        "radiation",
    }
)


def _coerce_finite_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalise_metric_value(metric: str, value: float, unit: str | None) -> float:
    # Current firmware may emit pressure in Pa or label Pa-like values as hPa.
    if metric == "pressure" and unit in {"Pa", "hPa"} and value > 2000.0:
        return value / 100.0
    return value


def _is_unknown_non_negative_metric(
    metric: str,
    non_negative_metrics: Collection[str],
) -> bool:
    metric_lower = metric.lower()
    return any(marker in metric_lower for marker in non_negative_metrics)


def check_sensor_value_range(
    metric: str,
    value: Any,
    unit: str | None = None,
    *,
    ranges: Mapping[str, SensorRange] = DEFAULT_SENSOR_RANGES,
    warn_on_unknown: bool = False,
    unknown_non_negative_metrics: Collection[str] = DEFAULT_NON_NEGATIVE_METRICS,
) -> SensorRangeCheck:
    """Check one sensor metric value against the default sanity ranges."""

    numeric_value = _coerce_finite_number(value)
    if numeric_value is None:
        return SensorRangeCheck(
            status="fail",
            metric=metric,
            value=None,
            rule=None,
            message=f"{metric} must be a finite number",
        )

    rule = ranges.get(metric)
    if rule is None:
        if numeric_value < 0.0 and _is_unknown_non_negative_metric(
            metric, unknown_non_negative_metrics
        ):
            return SensorRangeCheck(
                status="fail",
                metric=metric,
                value=numeric_value,
                rule=None,
                message=f"{metric} must be non-negative",
            )

        status: RangeStatus = "warn" if warn_on_unknown else "ok"
        return SensorRangeCheck(
            status=status,
            metric=metric,
            value=numeric_value,
            rule=None,
            message=f"{metric} has no configured range",
        )

    checked_value = _normalise_metric_value(metric, numeric_value, unit)
    if not rule.contains(checked_value):
        return SensorRangeCheck(
            status="fail",
            metric=metric,
            value=checked_value,
            rule=rule.name,
            message=f"{metric}={checked_value:g} is outside {rule.name}",
        )

    return SensorRangeCheck(
        status="ok",
        metric=metric,
        value=checked_value,
        rule=rule.name,
        message=f"{metric} is inside {rule.name}",
    )


def check_sensor_sample_range(
    sample: SensorSample | SensorSampleRecord,
    *,
    ranges: Mapping[str, SensorRange] = DEFAULT_SENSOR_RANGES,
    warn_on_unknown: bool = False,
    unknown_non_negative_metrics: Collection[str] = DEFAULT_NON_NEGATIVE_METRICS,
) -> SensorRangeCheck:
    """Check one parsed sensor sample against the default sanity ranges."""

    return check_sensor_value_range(
        sample.metric,
        sample.value,
        sample.unit,
        ranges=ranges,
        warn_on_unknown=warn_on_unknown,
        unknown_non_negative_metrics=unknown_non_negative_metrics,
    )


def check_sensor_sample_ranges(
    samples: Iterable[SensorSample | SensorSampleRecord],
    *,
    ranges: Mapping[str, SensorRange] = DEFAULT_SENSOR_RANGES,
    warn_on_unknown: bool = False,
    unknown_non_negative_metrics: Collection[str] = DEFAULT_NON_NEGATIVE_METRICS,
) -> SensorRangeReport:
    """Check parsed sensor samples against the default sanity ranges."""

    checks = tuple(
        check_sensor_sample_range(
            sample,
            ranges=ranges,
            warn_on_unknown=warn_on_unknown,
            unknown_non_negative_metrics=unknown_non_negative_metrics,
        )
        for sample in samples
    )
    findings = tuple(check for check in checks if check.status != "ok")
    warning_count = sum(1 for finding in findings if finding.status == "warn")
    failure_count = sum(1 for finding in findings if finding.status == "fail")

    if failure_count:
        status: RangeStatus = "fail"
        message = f"{failure_count} sensor samples are outside sane ranges"
    elif warning_count:
        status = "warn"
        message = f"{warning_count} sensor samples need range configuration"
    else:
        status = "ok"
        message = "All parsed sensor samples are inside sane ranges"

    return SensorRangeReport(
        status=status,
        checked_samples_count=len(checks),
        warning_count=warning_count,
        failure_count=failure_count,
        findings=findings,
        message=message,
    )
