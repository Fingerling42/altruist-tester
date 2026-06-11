from datetime import UTC, datetime, timedelta

from altruist_tester.rules.flatline import (
    check_sensor_flatlines,
    check_series_flatline,
)
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries


def _sample(
    sensor: str,
    metric: str,
    value: float,
    offset_seconds: int,
) -> SensorSampleRecord:
    ts = datetime(2026, 6, 5, 12, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    return SensorSampleRecord(
        ts=ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        sensor=sensor,
        metric=metric,
        value=value,
        unit=None,
        source="serial",
    )


def _series_with_records(*records: SensorSampleRecord) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for record in records:
        series.append(record)
    return series


def test_check_series_flatline_passes_for_changing_values():
    records = [
        _sample("BME280", "temperature", 24.0, 0),
        _sample("BME280", "temperature", 24.2, 60),
        _sample("BME280", "temperature", 24.3, 120),
    ]

    finding = check_series_flatline("BME280", "temperature", records)

    assert finding.status == "ok"
    assert finding.distinct_values_count == 3
    assert finding.elapsed_seconds == 120.0


def test_check_series_flatline_warns_when_there_is_not_enough_data():
    records = [_sample("SCD4x", "co2", 612.0, 0)]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "warn"
    assert finding.samples_count == 1
    assert "not enough samples" in finding.message


def test_check_series_flatline_warns_for_short_flatline_window():
    records = [
        _sample("SCD4x", "co2", 612.0, 0),
        _sample("SCD4x", "co2", 612.0, 30 * 60),
    ]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "warn"
    assert finding.distinct_values_count == 1
    assert finding.elapsed_seconds == 1800.0


def test_check_series_flatline_fails_for_long_flatline():
    records = [
        _sample("SCD4x", "co2", 612.0, 0),
        _sample("SCD4x", "co2", 612.0, 60 * 60),
    ]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "fail"
    assert finding.distinct_values_count == 1
    assert finding.elapsed_seconds == 3600.0


def test_check_series_flatline_warns_for_long_zero_flatline_metrics():
    records = [
        _sample("SDS", "P2", 0.0, 0),
        _sample("SDS", "P2", 0.0, 60 * 60),
    ]

    finding = check_series_flatline("SDS", "P2", records)

    assert finding.status == "warn"
    assert finding.canonical_metric == "pm25"
    assert finding.elapsed_seconds == 3600.0


def test_check_sensor_flatlines_aggregates_findings():
    series = _series_with_records(
        _sample("BME280", "temperature", 24.0, 0),
        _sample("BME280", "temperature", 24.2, 60),
        _sample("SCD4x", "co2", 612.0, 0),
        _sample("SCD4x", "co2", 612.0, 60 * 60),
    )

    report = check_sensor_flatlines(series)

    assert report.status == "fail"
    assert report.ok is False
    assert report.checked_series_count == 2
    assert report.failure_count == 1
    assert [finding.status for finding in report.findings] == ["ok", "fail"]
    assert report.as_dict()["failure_count"] == 1
