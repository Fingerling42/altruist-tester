from altruist_tester.parsers.upload_events import UploadEvent
from altruist_tester.rules.uploads import UploadChannelConfig, check_upload_health
from altruist_tester.uploads import UploadStats


def _stats(*events: UploadEvent) -> UploadStats:
    stats = UploadStats()
    for event in events:
        stats.append(event)
    return stats


def test_check_upload_health_skips_disabled_channels():
    report = check_upload_health(
        _stats(
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "failure", reason="HTTP error"),
        )
    )

    assert report.status == "ok"
    assert report.channels["connectivity"].mode == "disabled"
    assert report.channels["connectivity"].attempts == 1
    assert report.findings == ()


def test_check_upload_health_warns_for_optional_channel_failures():
    report = check_upload_health(
        _stats(
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "failure", reason="HTTP error"),
        ),
        connectivity=UploadChannelConfig(mode="optional", min_success_rate=0.8),
    )

    assert report.status == "warn"
    assert report.warning_count == 2
    assert report.channels["connectivity"].failures == 1
    assert report.channels["connectivity"].success_rate == 0.0


def test_check_upload_health_fails_required_channel_without_successes():
    report = check_upload_health(
        _stats(
            UploadEvent("datalog", "attempt"),
            UploadEvent("datalog", "failure", reason="subscription missing"),
        ),
        datalog=UploadChannelConfig(mode="required", min_successes=1),
    )

    assert report.status == "fail"
    assert report.failure_count == 2
    assert report.channels["datalog"].failure_reasons == {"subscription missing": 1}


def test_check_upload_health_accepts_required_success_rate():
    report = check_upload_health(
        _stats(
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "success"),
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "failure", reason="timeout"),
            UploadEvent("connectivity", "attempt"),
            UploadEvent("connectivity", "success"),
        ),
        connectivity=UploadChannelConfig(
            mode="required",
            min_successes=2,
            min_success_rate=0.5,
            max_consecutive_failures=1,
        ),
    )

    assert report.status == "ok"
    assert report.channels["connectivity"].attempts == 3
    assert report.channels["connectivity"].successes == 2
    assert report.channels["connectivity"].success_rate == 2 / 3


def test_check_upload_health_warns_for_attempt_without_previous_outcome():
    report = check_upload_health(
        _stats(
            UploadEvent("datalog", "attempt"),
            UploadEvent("datalog", "attempt"),
            UploadEvent("datalog", "success"),
        ),
        datalog=UploadChannelConfig(mode="optional"),
    )

    channel = report.channels["datalog"]
    assert report.status == "warn"
    assert channel.attempts == 2
    assert channel.warnings == 1
    assert channel.warning_reasons == {"attempt_without_previous_outcome": 1}
    assert report.findings[0].code == "UPLOAD_LOG_SEQUENCE_WARNING"
