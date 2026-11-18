import json

import pytest

from altruist_tester.parsers.sensor_values import parse_sensor_values
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
from tests.helpers import uart_snapshot


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


def _snapshot_series(sensors):
    series = SensorSampleSeries()
    payload = {
        sensor: {metric: {"value": 42} for metric in metrics}
        for sensor, metrics in sensors.items()
    }
    for sample in parse_sensor_values(json.dumps(payload)):
        series.append(
            SensorSampleRecord(ts="2026-11-18T12:00:00Z", **sample.as_payload())
        )
    return series


def test_insight_other_sources_cannot_confirm_missing_bme680():
    series = _snapshot_series(
        {
            "SCD4x": ("temperature", "humidity", "co2"),
            "altruist_urban": ("pressure", "temperature", "humidity"),
        }
    )
    report = check_sensor_presence(series, expected_sensors=("scd41", "bme680"))
    assert report.status == "fail"
    assert report.missing_metrics == ()
    assert report.missing_sensor_metrics == (
        ("bme680", "humidity"),
        ("bme680", "pressure"),
        ("bme680", "temperature"),
    )
    assert "bme680.pressure" in report.message
    assert report.as_dict()["missing_sensor_metrics"][0] == {
        "sensor": "bme680",
        "metric": "humidity",
    }


@pytest.mark.parametrize("source", ["altruist_urban", "BME680", "unknown_sensor"])
def test_foreign_or_aggregated_source_cannot_confirm_bme280(source):
    series = _snapshot_series({source: ("temperature", "humidity", "pressure")})
    report = check_sensor_presence(series, expected_sensors=("bme280",))
    assert report.status == "fail"
    assert report.missing_sensor_metrics == (
        ("bme280", "humidity"),
        ("bme280", "pressure"),
        ("bme280", "temperature"),
    )


@pytest.mark.parametrize(
    "sensor, alias, metrics",
    [
        ("BME680", "bme-680", ("temperature", "humidity", "pressure")),
        ("bme_280", "BME280", ("temperature", "humidity", "pressure")),
        ("SCD4x", "scd40", ("temperature", "humidity", "co2")),
        ("SDS011", "sds-011", ("P1", "P2")),
        ("I2S_noise", "ics-43434", ("noiseAvg", "noiseMax")),
    ],
)
def test_physical_sensor_and_metric_aliases_pass(sensor, alias, metrics):
    report = check_sensor_presence(
        _snapshot_series({sensor: metrics}), expected_sensors=(alias,)
    )
    assert report.status == "ok"
    assert report.missing_sensor_metrics == ()


def test_each_physical_sensor_must_supply_each_required_metric():
    series = _snapshot_series(
        {
            "BME680": ("pressure",),
            "SCD4x": ("temperature", "humidity", "co2"),
        }
    )
    report = check_sensor_presence(series, expected_sensors=("bme680", "scd41"))
    assert report.status == "fail"
    assert report.missing_sensor_metrics == (
        ("bme680", "humidity"),
        ("bme680", "temperature"),
    )


@pytest.mark.parametrize("channel", ["datalog", "sensors-connectivity"])
def test_payload_cannot_satisfy_sensor_or_metric_expectations(channel):
    series = SensorSampleSeries()
    compact = "h:50,t:20,p:100000"
    line = (
        f"[PAYLOAD] channel={channel} encoding=plain encrypted=0 "
        f"payload_len={len(compact)} sample={compact}"
    )
    for sample in parse_sensor_values(line):
        series.append(
            SensorSampleRecord(ts="2026-11-18T12:00:00Z", **sample.as_payload())
        )
    assert series.count() == 0
    report = check_sensor_presence(series, expected_sensors=("bme680",))
    assert report.status == "fail"
    assert report.missing_metrics == ("humidity", "pressure", "temperature")
    assert len(report.missing_sensor_metrics) == 3
    assert (
        check_sensor_presence(
            series, expected_metrics=("temperature", "humidity", "pressure")
        ).status
        == "fail"
    )


def test_named_sensor_metrics_do_not_cover_an_unobserved_metric():
    series = _snapshot_series(
        {
            "BME680": ("temperature", "humidity", "pressure"),
        }
    )
    report = check_sensor_presence(
        series, expected_sensors=("bme680",), expected_metrics=("co2",)
    )
    assert report.status == "fail"
    assert report.missing_metrics == ("co2",)
    assert report.missing_sensor_metrics == ()


def test_metric_only_expectations_accept_versioned_sensor_measurements():
    series = SensorSampleSeries()
    for sample in parse_sensor_values(
        json.dumps(uart_snapshot("BME280", {"temperature": 24, "humidity": 45}))
    ):
        assert sample.source == "serial_observation_v2"
        series.append(
            SensorSampleRecord(ts="2026-11-18T12:00:00Z", **sample.as_payload())
        )

    report = check_sensor_presence(series, expected_metrics=("temperature", "humidity"))

    assert report.status == "ok"
    assert report.as_dict() == {
        "status": "ok",
        "expected_sensors": [],
        "expected_metrics": ["humidity", "temperature"],
        "observed_metrics": ["humidity", "temperature"],
        "missing_metrics": [],
        "message": "All expected sensor metrics were observed",
        "missing_sensor_metrics": [],
    }
