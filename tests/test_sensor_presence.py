from altruist_tester.rules.presence import (
    UnknownExpectedSensorError,
    canonical_metric_name,
    canonical_sensor_name,
    check_sensor_presence,
    expected_metrics_for_sensors,
    known_expected_sensors,
    observed_metrics_from_series,
)
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries


def _series_with_metrics(*metrics: str) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for index, metric in enumerate(metrics):
        series.append(
            SensorSampleRecord(
                ts=f"2026-06-05T12:00:{index:02d}.000Z",
                sensor="sensor",
                metric=metric,
                value=float(index),
                unit=None,
                source="serial",
            )
        )
    return series


def test_canonical_metric_name_maps_firmware_pm_aliases():
    assert canonical_metric_name("P1") == "pm10"
    assert canonical_metric_name("P2") == "pm25"
    assert canonical_metric_name("pm2.5") == "pm25"
    assert canonical_metric_name("temperature") == "temperature"


def test_expected_sensor_presets_expand_to_expected_metrics():
    assert canonical_sensor_name("ICS-43434") == "ics43434"
    assert known_expected_sensors() == (
        "bme280",
        "bme680",
        "ics43434",
        "scd41",
        "sds",
    )
    assert expected_metrics_for_sensors(("BME280", "SDS", "ICS-43434")) == (
        "humidity",
        "noise_avg",
        "noise_max",
        "pm10",
        "pm25",
        "pressure",
        "temperature",
    )
    assert expected_metrics_for_sensors(("SCD41", "BME680")) == (
        "co2",
        "humidity",
        "pressure",
        "temperature",
    )


def test_expected_sensor_presets_reject_unknown_sensors():
    try:
        expected_metrics_for_sensors(("not-a-sensor",))
    except UnknownExpectedSensorError as exc:
        assert exc.sensor == "not-a-sensor"
        assert "Known sensors" in str(exc)
    else:
        raise AssertionError("expected UnknownExpectedSensorError")


def test_observed_metrics_from_series_returns_canonical_metrics():
    series = _series_with_metrics("temperature", "P1", "P2", "P2")

    assert observed_metrics_from_series(series) == ("pm10", "pm25", "temperature")


def test_check_sensor_presence_warns_without_expected_metrics():
    series = _series_with_metrics("temperature")

    report = check_sensor_presence(series)

    assert report.status == "warn"
    assert report.ok is True
    assert report.expected_metrics == ()
    assert report.observed_metrics == ("temperature",)


def test_check_sensor_presence_fails_when_expected_metric_is_missing():
    series = _series_with_metrics("temperature", "humidity", "P1")

    report = check_sensor_presence(
        series,
        expected_metrics=("temperature", "humidity", "pm10", "pm25"),
    )

    assert report.status == "fail"
    assert report.ok is False
    assert report.missing_metrics == ("pm25",)
    assert report.as_dict()["missing_metrics"] == ["pm25"]


def test_check_sensor_presence_uses_expected_sensor_presets():
    series = _series_with_metrics("temperature", "humidity", "pressure", "P1")

    report = check_sensor_presence(series, expected_sensors=("bme280", "sds"))

    assert report.status == "fail"
    assert report.expected_sensors == ("bme280", "sds")
    assert report.expected_metrics == (
        "humidity",
        "pm10",
        "pm25",
        "pressure",
        "temperature",
    )
    assert report.missing_metrics == ("pm25",)


def test_check_sensor_presence_passes_when_all_expected_metrics_are_seen():
    series = _series_with_metrics("temperature", "humidity", "P1", "P2")

    report = check_sensor_presence(
        series,
        expected_metrics=("temperature", "humidity", "pm10", "pm25"),
    )

    assert report.status == "ok"
    assert report.ok is True
    assert report.missing_metrics == ()
