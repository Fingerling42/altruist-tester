from datetime import UTC, datetime, timedelta

from altruist_tester.parsers.upload_events import UploadEvent
from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
from altruist_tester.rules.uploads import UploadChannelConfig
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
from altruist_tester.serial_logger import BuildEventsSummary, SerialLogStats
from altruist_tester.uploads import UploadStats


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


def _series(*records: SensorSampleRecord) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for record in records:
        series.append(record)
    return series


def _upload_stats(*events: UploadEvent) -> UploadStats:
    stats = UploadStats()
    for event in events:
        stats.append(event)
    return stats


def _build_events() -> BuildEventsSummary:
    return BuildEventsSummary(
        count=1,
        last_build={
            "version": "R-URB_2026-07-08",
            "channel": "stable",
            "commit": "abc1234",
            "model": "urban",
            "target": "esp32c6",
            "language": "en",
            "profile": "release",
        },
    )


def test_evaluate_rules_returns_pass_candidate_without_findings():
    stats = SerialLogStats(
        lines_read=10,
        bytes_read=100,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=50.0,
        max_interline_gap_seconds=10.0,
        dev_metrics_records=(
            {"boot": 1, "uptime_sec": 10},
            {"boot": 1, "uptime_sec": 20, "reset_reason": "power_on_reset"},
        ),
        sensor_series=_series(
            _sample("BME280", "temperature", 24.0, 0),
            _sample("BME280", "temperature", 24.5, 60),
        ),
        build_events=_build_events(),
    )

    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_metrics=("temperature",),
            duration_seconds=60,
        ),
    )

    assert result.verdict == "PASS_CANDIDATE"
    assert result.status == "ok"
    assert result.findings == ()
    assert result.failed_checks == ()


def test_evaluate_rules_returns_warn_for_non_failing_findings():
    stats = SerialLogStats(
        lines_read=2,
        bytes_read=20,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=10.0,
        max_interline_gap_seconds=9.0,
        dev_metrics_records=({"boot": 1, "uptime_sec": 10},),
        sensor_series=_series(
            _sample("BME280", "temperature", 24.0, 0),
        ),
    )

    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_metrics=("temperature",),
            duration_seconds=20,
        ),
    )

    assert result.verdict == "WARN"
    assert result.status == "warn"
    assert any(finding.severity == "warn" for finding in result.findings)


def test_evaluate_rules_lets_fail_dominate_warn():
    stats = SerialLogStats(
        lines_read=0,
        bytes_read=0,
        dev_metrics_records=(),
        sensor_series=_series(
            _sample("BME280", "humidity", 120.0, 0),
        ),
    )

    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_metrics=("temperature",),
            duration_seconds=10 * 60,
        ),
    )

    assert result.verdict == "FAIL"
    assert result.status == "fail"
    assert "sensor_presence" in result.failed_checks
    assert "sensor_ranges" in result.failed_checks
    assert "serial_silence" in result.failed_checks
    assert any(finding.severity == "warn" for finding in result.findings)
    assert any(finding.severity == "fail" for finding in result.findings)


def test_evaluate_rules_includes_subsystem_health_findings():
    stats = SerialLogStats(
        lines_read=10,
        bytes_read=100,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=50.0,
        max_interline_gap_seconds=10.0,
        dev_metrics_records=(
            {"boot": 1, "uptime_sec": 10},
            {"boot": 1, "uptime_sec": 20},
        ),
        sensor_series=_series(
            _sample("BME280", "temperature", 24.0, 0),
            _sample("BME280", "temperature", 24.5, 60),
        ),
        subsystem_event_records=(
            {
                "ts": "2026-07-24T12:00:00.000Z",
                "level": "error",
                "subsystem": "sd",
                "reason": "open_append_failed",
            },
        ),
    )

    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_metrics=("temperature",),
            duration_seconds=60,
        ),
    )

    assert result.verdict == "FAIL"
    assert "subsystem_health" in result.failed_checks
    assert result.reports.subsystem_health.status == "fail"
    assert result.findings[-1].code == "SUBSYSTEM_SD_OPEN_APPEND_FAILED_FAIL"
    assert result.findings[-1].rule == "subsystem_health"


def test_evaluate_rules_can_require_upload_success():
    stats = SerialLogStats(
        lines_read=10,
        bytes_read=100,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=50.0,
        max_interline_gap_seconds=10.0,
        dev_metrics_records=(
            {"boot": 1, "uptime_sec": 10},
            {"boot": 1, "uptime_sec": 20},
        ),
        sensor_series=_series(
            _sample("BME280", "temperature", 24.0, 0),
            _sample("BME280", "temperature", 24.5, 60),
        ),
        upload_stats=_upload_stats(
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "failure", reason="server returned HTTP error"),
        ),
    )

    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_metrics=("temperature",),
            duration_seconds=60,
            connectivity_upload=UploadChannelConfig(mode="required"),
        ),
    )

    assert result.verdict == "FAIL"
    assert "upload_health" in result.failed_checks
    assert result.reports.upload_health.channels["connectivity"].failures == 1
