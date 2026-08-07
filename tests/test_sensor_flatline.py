from altruist_tester.rules.flatline import (
    check_sensor_flatlines,
    check_series_flatline,
)
from tests.helpers import sample_record, sample_series


def test_check_series_flatline_passes_for_changing_values():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.2, 60),
        sample_record("BME280", "temperature", 24.3, 120),
    ]

    finding = check_series_flatline("BME280", "temperature", records)

    assert finding.status == "ok"
    assert finding.distinct_values_count == 3
    assert finding.elapsed_seconds == 120.0


def test_check_series_flatline_warns_when_there_is_not_enough_data():
    records = [sample_record("SCD4x", "co2", 612.0, 0)]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "warn"
    assert finding.samples_count == 1
    assert "not enough samples" in finding.message


def test_check_series_flatline_warns_for_short_flatline_window():
    records = [
        sample_record("SCD4x", "co2", 612.0, 0),
        sample_record("SCD4x", "co2", 612.0, 30 * 60),
    ]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "warn"
    assert finding.distinct_values_count == 1
    assert finding.elapsed_seconds == 1800.0


def test_check_series_flatline_fails_for_long_flatline():
    records = [
        sample_record("SCD4x", "co2", 612.0, 0),
        sample_record("SCD4x", "co2", 612.0, 60 * 60),
    ]

    finding = check_series_flatline("SCD4x", "co2", records)

    assert finding.status == "fail"
    assert finding.distinct_values_count == 1
    assert finding.elapsed_seconds == 3600.0


def test_check_series_flatline_warns_for_long_zero_flatline_metrics():
    # Zero particulate/gas/radiation readings can be physically valid, so a
    # long zero flatline is suspicious but not an automatic failure.
    records = [
        sample_record("SDS", "P2", 0.0, 0),
        sample_record("SDS", "P2", 0.0, 60 * 60),
    ]

    finding = check_series_flatline("SDS", "P2", records)

    assert finding.status == "warn"
    assert finding.canonical_metric == "pm25"
    assert finding.elapsed_seconds == 3600.0


def test_check_sensor_flatlines_aggregates_findings():
    series = sample_series(
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.2, 60),
        sample_record("SCD4x", "co2", 612.0, 0),
        sample_record("SCD4x", "co2", 612.0, 60 * 60),
    )

    report = check_sensor_flatlines(series)

    assert report.status == "fail"
    assert report.ok is False
    assert report.checked_series_count == 2
    assert report.failure_count == 1
    assert [finding.status for finding in report.findings] == ["ok", "fail"]
    assert report.as_dict()["failure_count"] == 1
