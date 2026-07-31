from altruist_tester.parsers.upload_events import UploadEvent
from altruist_tester.rules.log_contract import check_log_contract
from altruist_tester.rules.uploads import UploadChannelConfig
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
from altruist_tester.serial_logger import SerialLogStats
from altruist_tester.uploads import UploadStats


def _series() -> SensorSampleSeries:
    series = SensorSampleSeries()
    series.append(
        SensorSampleRecord(
            ts="2026-07-31T12:00:00.000Z",
            sensor="BME280",
            metric="temperature",
            value=24.0,
            unit=None,
            source="serial",
        )
    )
    return series


def _late_series() -> SensorSampleSeries:
    series = SensorSampleSeries()
    series.append(
        SensorSampleRecord(
            ts="2026-07-31T12:20:00.000Z",
            sensor="BME280",
            metric="temperature",
            value=24.0,
            unit=None,
            source="serial",
        )
    )
    return series


def _upload_stats(*events: UploadEvent) -> UploadStats:
    stats = UploadStats()
    for event in events:
        stats.append(event)
    return stats


def _check(stats: SerialLogStats, **kwargs):
    return check_log_contract(
        stats,
        startup_window_seconds=10 * 60,
        connectivity_upload=kwargs.get("connectivity", UploadChannelConfig()),
        datalog_upload=kwargs.get("datalog", UploadChannelConfig()),
    )


def test_log_contract_passes_with_required_release_telemetry():
    report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 10, "reset_reason": "power_on_reset"},
            ),
            sensor_series=_series(),
        )
    )

    assert report.status == "ok"
    assert report.findings == ()
    assert report.signals["health_telemetry"] is True
    assert report.signals["sensor_samples"] is True
    assert report.signals["boot_or_reset_context"] is True


def test_log_contract_fails_without_health_and_sensor_samples():
    report = _check(SerialLogStats(lines_read=10, bytes_read=100))

    assert report.status == "fail"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_HEALTH_MISSING",
        "LOG_CONTRACT_SENSOR_SAMPLES_MISSING",
        "LOG_CONTRACT_BOOT_CONTEXT_MISSING",
    ]
    assert [finding.status for finding in report.findings] == [
        "fail",
        "fail",
        "warn",
    ]


def test_log_contract_warns_without_boot_or_reset_context():
    report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=({"boot": 1, "uptime_sec": 10},),
            sensor_series=_series(),
        )
    )

    assert report.status == "warn"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_BOOT_CONTEXT_MISSING"
    ]


def test_log_contract_requires_upload_telemetry_only_for_enabled_channels():
    disabled_report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 10, "reset_reason": "power_on_reset"},
            ),
            sensor_series=_series(),
        )
    )
    required_report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 10, "reset_reason": "power_on_reset"},
            ),
            sensor_series=_series(),
        ),
        connectivity=UploadChannelConfig(mode="required"),
    )
    observed_report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 10, "reset_reason": "power_on_reset"},
            ),
            sensor_series=_series(),
            upload_stats=_upload_stats(UploadEvent("connectivity", "attempt")),
        ),
        connectivity=UploadChannelConfig(mode="required"),
    )

    assert disabled_report.status == "ok"
    assert required_report.status == "fail"
    assert (
        required_report.findings[0].code == "LOG_CONTRACT_CONNECTIVITY_UPLOAD_MISSING"
    )
    assert observed_report.status == "ok"


def test_log_contract_flags_late_release_telemetry():
    report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            capture_started_at="2026-07-31T12:00:00.000Z",
            dev_metrics_records=(
                {
                    "ts": "2026-07-31T12:20:00.000Z",
                    "boot": 1,
                    "uptime_sec": 10,
                    "reset_reason": "power_on_reset",
                },
            ),
            sensor_series=_late_series(),
        )
    )

    assert report.status == "fail"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_HEALTH_LATE",
        "LOG_CONTRACT_SENSOR_SAMPLES_LATE",
    ]
    assert report.signals["health_telemetry_in_startup_window"] is False
    assert report.signals["sensor_samples_in_startup_window"] is False
