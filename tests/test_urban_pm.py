from altruist_tester.rules.urban_pm import check_urban_pm_nearly_zero
from altruist_tester.samples import SensorSampleRecord
from tests.helpers import sample_record, sample_series


def _pm_sample(
    metric: str,
    value: float,
    offset_seconds: int,
) -> SensorSampleRecord:
    return sample_record("datalog", metric, value, offset_seconds)


def test_check_urban_pm_nearly_zero_warns_on_suspicious_long_pm_series():
    series = sample_series(
        *(_pm_sample("P1", 0.0, index * 60) for index in range(95)),
        *(_pm_sample("P1", 0.18, index * 60) for index in range(95, 100)),
    )

    report = check_urban_pm_nearly_zero(series, enabled=True)

    assert report.status == "warn"
    assert report.warning_count == 1
    assert report.findings[0].canonical_metric == "pm10"
    assert report.findings[0].zero_count == 95
    assert report.findings[0].max_value == 0.18


def test_check_urban_pm_nearly_zero_passes_when_pm_has_visible_activity():
    series = sample_series(
        *(_pm_sample("P2", 0.0, index * 60) for index in range(50)),
        *(_pm_sample("P2", 3.0, index * 60) for index in range(50, 100)),
    )

    report = check_urban_pm_nearly_zero(series, enabled=True)

    assert report.status == "ok"
    assert report.findings == ()


def test_check_urban_pm_nearly_zero_is_disabled_for_non_urban_devices():
    series = sample_series(
        *(_pm_sample("P1", 0.0, index * 60) for index in range(100)),
    )

    report = check_urban_pm_nearly_zero(series, enabled=False)

    assert report.status == "ok"
    assert report.checked_series_count == 0
