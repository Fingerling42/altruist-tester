from altruist_tester.parsers.upload_events import UploadEvent
from altruist_tester.rules.log_contract import check_log_contract
from altruist_tester.rules.uploads import UploadChannelConfig
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
from altruist_tester.serial_logger import BuildEventsSummary, SerialLogStats
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


def _build_events(*, first_seen: str | None = None) -> BuildEventsSummary:
    return BuildEventsSummary(
        count=1,
        first_seen=first_seen,
        last_seen=first_seen,
        last_build={
            "version": "R-URB_2026-07-08",
            "channel": "stable",
            "commit": "abc1234",
            "model": "urban",
            "target": "esp32c6",
            "language": "en",
            "profile": "release",
            "raw_fields": {},
        },
    )


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
            build_events=_build_events(),
        )
    )

    assert report.status == "ok"
    assert report.findings == ()
    assert report.signals["health_telemetry"] is True
    assert report.signals["sensor_samples"] is True
    assert report.signals["firmware_build_info"] is True
    assert report.signals["boot_or_reset_context"] is True


def test_log_contract_fails_without_health_and_sensor_samples():
    report = _check(SerialLogStats(lines_read=10, bytes_read=100))

    assert report.status == "fail"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_HEALTH_MISSING",
        "LOG_CONTRACT_SENSOR_SAMPLES_MISSING",
        "LOG_CONTRACT_BOOT_CONTEXT_MISSING",
        "LOG_CONTRACT_BUILD_INFO_MISSING",
    ]
    assert [finding.status for finding in report.findings] == [
        "fail",
        "fail",
        "warn",
        "warn",
    ]


def test_log_contract_warns_without_boot_or_reset_context():
    report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            dev_metrics_records=({"boot": 1, "uptime_sec": 10},),
            sensor_series=_series(),
            build_events=_build_events(),
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
            build_events=_build_events(),
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
            build_events=_build_events(),
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
            build_events=_build_events(),
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
            build_events=_build_events(first_seen="2026-07-31T12:00:00.000Z"),
        )
    )

    assert report.status == "fail"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_HEALTH_LATE",
        "LOG_CONTRACT_SENSOR_SAMPLES_LATE",
    ]
    assert report.signals["health_telemetry_in_startup_window"] is False
    assert report.signals["sensor_samples_in_startup_window"] is False


def test_log_contract_warns_when_build_info_is_late():
    report = _check(
        SerialLogStats(
            lines_read=10,
            bytes_read=100,
            capture_started_at="2026-07-31T12:00:00.000Z",
            dev_metrics_records=(
                {
                    "ts": "2026-07-31T12:00:05.000Z",
                    "boot": 1,
                    "uptime_sec": 10,
                    "reset_reason": "power_on_reset",
                },
            ),
            sensor_series=_series(),
            build_events=_build_events(first_seen="2026-07-31T12:20:00.000Z"),
        )
    )

    assert report.status == "warn"
    assert [finding.code for finding in report.findings] == [
        "LOG_CONTRACT_FIRMWARE_BUILD_INFO_LATE"
    ]
    assert report.signals["firmware_build_info_in_startup_window"] is False
