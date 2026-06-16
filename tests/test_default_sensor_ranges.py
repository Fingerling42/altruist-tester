import math

from altruist_tester.rules.defaults import (
    DEFAULT_SENSOR_RANGES,
    check_sensor_sample_range,
    check_sensor_sample_ranges,
    check_sensor_value_range,
)
from altruist_tester.samples import SensorSample


def test_default_ranges_include_expected_sensor_metrics():
    assert DEFAULT_SENSOR_RANGES["temperature"].name == "temperature_c"
    assert DEFAULT_SENSOR_RANGES["humidity"].name == "humidity_percent"
    assert DEFAULT_SENSOR_RANGES["pressure"].name == "pressure_hpa"
    assert DEFAULT_SENSOR_RANGES["P1"].name == "pm_ug_m3"
    assert DEFAULT_SENSOR_RANGES["P2"].name == "pm_ug_m3"
    assert DEFAULT_SENSOR_RANGES["co2"].name == "co2_ppm"
    assert DEFAULT_SENSOR_RANGES["noiseAvg"].name == "noise_dba"
    assert DEFAULT_SENSOR_RANGES["radiation"].non_negative is True
    assert DEFAULT_SENSOR_RANGES["co"].non_negative is True


def test_check_sensor_value_range_accepts_value_inside_range():
    result = check_sensor_value_range("temperature", 24.5, "°C")

    assert result.status == "ok"
    assert result.ok is True
    assert result.rule == "temperature_c"
    assert result.value == 24.5


def test_check_sensor_value_range_fails_value_outside_range():
    result = check_sensor_value_range("humidity", 120.0, "%")

    assert result.status == "fail"
    assert result.ok is False
    assert result.rule == "humidity_percent"


def test_check_sensor_value_range_fails_non_finite_or_empty_values():
    assert check_sensor_value_range("temperature", math.nan).status == "fail"
    assert check_sensor_value_range("temperature", math.inf).status == "fail"
    assert check_sensor_value_range("temperature", "").status == "fail"
    assert check_sensor_value_range("temperature", None).status == "fail"


def test_check_sensor_value_range_accepts_unknown_finite_metric_by_default():
    result = check_sensor_value_range("custom_metric", 42.0)

    assert result.status == "ok"
    assert result.ok is True
    assert result.rule is None


def test_check_sensor_value_range_can_warn_for_unknown_metric():
    result = check_sensor_value_range("custom_metric", 42.0, warn_on_unknown=True)

    assert result.status == "warn"
    assert result.ok is True


def test_check_sensor_value_range_fails_unknown_negative_non_negative_metric():
    result = check_sensor_value_range("custom_gas_sensor", -0.1)

    assert result.status == "fail"
    assert result.message == "custom_gas_sensor must be non-negative"


def test_check_sensor_sample_range_uses_sample_metric_value_and_unit():
    sample = SensorSample(
        sensor="BME280",
        metric="pressure",
        value=101069.1,
        unit="hPa",
    )

    result = check_sensor_sample_range(sample)

    assert result.status == "ok"
    assert result.rule == "pressure_hpa"
    assert result.value == 1010.691


def test_check_sensor_sample_ranges_aggregates_non_ok_findings():
    samples = [
        SensorSample(
            sensor="BME280",
            metric="humidity",
            value=45.0,
            unit="%",
        ),
        SensorSample(
            sensor="BME280",
            metric="humidity",
            value=120.0,
            unit="%",
        ),
    ]

    report = check_sensor_sample_ranges(samples)

    assert report.status == "fail"
    assert report.checked_samples_count == 2
    assert report.failure_count == 1
    assert len(report.findings) == 1
    assert report.findings[0].message == "humidity=120 is outside humidity_percent"
