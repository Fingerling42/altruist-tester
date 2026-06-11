"""Sensor data presence checks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from altruist_tester.samples import SensorSampleSeries

PresenceStatus = Literal["ok", "warn", "fail"]

SENSOR_ALIASES = {
    "bme_280": "bme280",
    "bme-280": "bme280",
    "bme280": "bme280",
    "sds": "sds",
    "sds_011": "sds",
    "sds-011": "sds",
    "sds011": "sds",
    "ics_43434": "ics43434",
    "ics43434": "ics43434",
    "ics-43434": "ics43434",
    "i2s_noise": "ics43434",
    "noise": "ics43434",
    "scd_40": "scd41",
    "scd-40": "scd41",
    "scd40": "scd41",
    "scd_41": "scd41",
    "scd-41": "scd41",
    "scd41": "scd41",
    "scd_4x": "scd41",
    "scd-4x": "scd41",
    "scd4x": "scd41",
    "bme_680": "bme680",
    "bme-680": "bme680",
    "bme680": "bme680",
}

SENSOR_EXPECTED_METRICS = {
    "bme280": ("humidity", "pressure", "temperature"),
    "sds": ("pm10", "pm25"),
    "ics43434": ("noise_avg", "noise_max"),
    "scd41": ("co2", "humidity", "temperature"),
    "bme680": ("humidity", "pressure", "temperature"),
}

METRIC_ALIASES = {
    "p1": "pm10",
    "pm1": "pm1",
    "pm10": "pm10",
    "p2": "pm25",
    "pm2.5": "pm25",
    "pm2_5": "pm25",
    "pm25": "pm25",
    "noiseavg": "noise_avg",
    "noise_avg": "noise_avg",
    "noisemax": "noise_max",
    "noise_max": "noise_max",
}


class UnknownExpectedSensorError(ValueError):
    """Raised when an expected sensor preset is not known."""

    def __init__(self, sensor: str) -> None:
        self.sensor = sensor
        known = ", ".join(known_expected_sensors())
        super().__init__(f"Unknown expected sensor {sensor!r}. Known sensors: {known}")


@dataclass(frozen=True, slots=True)
class SensorPresenceReport:
    """Result of checking whether expected sensor metrics were observed."""

    status: PresenceStatus
    expected_sensors: tuple[str, ...]
    expected_metrics: tuple[str, ...]
    observed_metrics: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the presence check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "expected_sensors": list(self.expected_sensors),
            "expected_metrics": list(self.expected_metrics),
            "observed_metrics": list(self.observed_metrics),
            "missing_metrics": list(self.missing_metrics),
            "message": self.message,
        }


def canonical_metric_name(metric: str) -> str:
    """Return the canonical metric name used by presence checks."""

    cleaned = metric.strip()
    key = cleaned.lower()
    return METRIC_ALIASES.get(key, key)


def canonical_sensor_name(sensor: str) -> str:
    """Return the canonical expected sensor preset name."""

    cleaned = sensor.strip()
    key = cleaned.lower().replace(" ", "_")
    return SENSOR_ALIASES.get(key, key)


def known_expected_sensors() -> tuple[str, ...]:
    """Return known expected sensor preset names."""

    return tuple(sorted(SENSOR_EXPECTED_METRICS))


def _unique_sorted_metrics(metrics: Iterable[str]) -> tuple[str, ...]:
    unique_metrics = {canonical_metric_name(metric) for metric in metrics if metric}
    return tuple(sorted(unique_metrics))


def _unique_sorted_sensors(sensors: Iterable[str]) -> tuple[str, ...]:
    unique_sensors = {canonical_sensor_name(sensor) for sensor in sensors if sensor}
    return tuple(sorted(unique_sensors))


def expected_metrics_for_sensors(sensors: Sequence[str]) -> tuple[str, ...]:
    """Return canonical expected metrics for expected sensor presets."""

    metrics = []
    for sensor in _unique_sorted_sensors(sensors):
        sensor_metrics = SENSOR_EXPECTED_METRICS.get(sensor)
        if sensor_metrics is None:
            raise UnknownExpectedSensorError(sensor)
        metrics.extend(sensor_metrics)
    return _unique_sorted_metrics(metrics)


def observed_metrics_from_series(series: SensorSampleSeries) -> tuple[str, ...]:
    """Return canonical metric names that have at least one sample."""

    return _unique_sorted_metrics(
        metric for (_sensor, metric), records in series.by_key.items() if records
    )


def check_sensor_presence(
    series: SensorSampleSeries,
    *,
    expected_metrics: Sequence[str] = (),
    expected_sensors: Sequence[str] = (),
) -> SensorPresenceReport:
    """Check that expected metrics were observed at least once."""

    observed_metrics = observed_metrics_from_series(series)
    expected_sensor_names = _unique_sorted_sensors(expected_sensors)
    expected = _unique_sorted_metrics(
        (*expected_metrics, *expected_metrics_for_sensors(expected_sensor_names))
    )

    if not expected:
        return SensorPresenceReport(
            status="warn",
            expected_sensors=(),
            expected_metrics=(),
            observed_metrics=observed_metrics,
            missing_metrics=(),
            message="Mandatory sensor metrics are not configured",
        )

    observed_set = set(observed_metrics)
    missing = tuple(metric for metric in expected if metric not in observed_set)
    if missing:
        return SensorPresenceReport(
            status="fail",
            expected_sensors=expected_sensor_names,
            expected_metrics=expected,
            observed_metrics=observed_metrics,
            missing_metrics=missing,
            message="Missing expected sensor metrics: " + ", ".join(missing),
        )

    return SensorPresenceReport(
        status="ok",
        expected_sensors=expected_sensor_names,
        expected_metrics=expected,
        observed_metrics=observed_metrics,
        missing_metrics=(),
        message="All expected sensor metrics were observed",
    )
