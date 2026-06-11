"""Default sensor value ranges for basic sanity checks."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
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
    # Current firmware labels pressure as hPa while emitting Pa-like values.
    if metric == "pressure" and unit == "hPa" and value > 2000.0:
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
