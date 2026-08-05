from datetime import UTC, datetime

from altruist_tester.rules.cadence import (
    check_sensor_cadence,
    check_series_cadence,
)
from tests.helpers import sample_record, sample_series


def test_check_series_cadence_passes_for_regular_updates():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 5 * 60),
        sample_record("BME280", "temperature", 24.2, 10 * 60),
    ]

    finding = check_series_cadence("BME280", "temperature", records)

    assert finding.status == "ok"
    assert finding.max_gap_seconds == 300.0
    assert finding.tail_gap_seconds is None


def test_check_series_cadence_warns_after_two_missed_updates():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 15 * 60),
    ]

    finding = check_series_cadence("BME280", "temperature", records)

    assert finding.status == "warn"
    assert finding.max_gap_seconds == 900.0
    assert finding.warn_after_seconds == 600


def test_check_series_cadence_fails_after_four_missed_updates():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 25 * 60),
    ]

    finding = check_series_cadence("BME280", "temperature", records)

    assert finding.status == "fail"
    assert finding.max_gap_seconds == 1500.0
    assert finding.fail_after_seconds == 1200


def test_check_series_cadence_checks_tail_gap_to_reference_time():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 5 * 60),
    ]
    reference_time = datetime(2026, 6, 5, 12, 30, tzinfo=UTC)

    finding = check_series_cadence(
        "BME280",
        "temperature",
        records,
        reference_time=reference_time,
    )

    assert finding.status == "fail"
    assert finding.tail_gap_seconds == 1500.0
    assert finding.max_gap_seconds == 1500.0


def test_check_series_cadence_caps_tail_gap_to_run_window():
    records = [
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 5 * 60),
    ]
    # Use a far-future reference to verify the cap is the test run window, not
    # wall-clock distance from the last sample.
    reference_time = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)

    finding = check_series_cadence(
        "BME280",
        "temperature",
        records,
        reference_time=reference_time,
        max_tail_window_seconds=10 * 60,
    )

    assert finding.status == "warn"
    assert finding.tail_gap_seconds == 600.0
    assert finding.max_gap_seconds == 600.0


def test_check_sensor_cadence_aggregates_findings():
    series = sample_series(
        sample_record("BME280", "temperature", 24.0, 0),
        sample_record("BME280", "temperature", 24.1, 5 * 60),
        sample_record("SCD4x", "co2", 612.0, 0),
        sample_record("SCD4x", "co2", 613.0, 25 * 60),
    )

    report = check_sensor_cadence(series)

    assert report.status == "fail"
    assert report.ok is False
    assert report.checked_series_count == 2
    assert report.failure_count == 1
    assert [finding.status for finding in report.findings] == ["ok", "fail"]
    assert report.as_dict()["failure_count"] == 1
