from altruist_tester.samples import (
    SensorSample,
    SensorSampleRecord,
    SensorSampleSeries,
)


def test_sensor_sample_has_stable_payload_and_key():
    sample = SensorSample(
        sensor="BME280",
        metric="temperature",
        value=24.5,
        unit="C",
    )

    assert sample.key == ("BME280", "temperature")
    assert sample.as_payload() == {
        "sensor": "BME280",
        "metric": "temperature",
        "value": 24.5,
        "unit": "C",
        "source": "serial",
    }


def test_sensor_sample_series_groups_records_by_sensor_and_metric():
    series = SensorSampleSeries()
    first = SensorSampleRecord(
        ts="2026-06-05T12:00:00.000Z",
        sensor="BME280",
        metric="temperature",
        value=24.5,
        unit="C",
        source="serial",
    )
    second = SensorSampleRecord(
        ts="2026-06-05T12:00:01.000Z",
        sensor="BME280",
        metric="temperature",
        value=24.6,
        unit="C",
        source="serial",
    )
    humidity = SensorSampleRecord(
        ts="2026-06-05T12:00:02.000Z",
        sensor="BME280",
        metric="humidity",
        value=45.0,
        unit="%",
        source="serial",
    )

    series.append(first)
    series.append(second)
    series.append(humidity)

    assert series.count() == 3
    assert series.latest(("BME280", "temperature")) == second
    assert series.latest(("BME280", "humidity")) == humidity
    assert series.latest(("SDS", "P1")) is None
