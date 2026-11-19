import pytest

from altruist_tester.parsers.upload_events import UploadEvent, parse_upload_event
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


@pytest.mark.parametrize(
    "mode,missing_status",
    [("required", "fail"), ("optional", "warn"), ("disabled", "ok")],
)
@pytest.mark.parametrize(
    "outcome", ["success", "failed reason=http_error code=500", None]
)
def test_health_probes_are_separate_from_delivery(mode, missing_status, outcome):
    lines = [
        f"[CONNECTIVITY] failed channel=sensors-connectivity seq=7 "
        f"stage=health reason={reason} host=probe code=-1 extension=kept"
        for reason in ["health_get_failed", "health_begin_failed", "health_get_failed"]
    ]
    if outcome:
        lines += ["[CONNECTIVITY] attempt channel=sensors-connectivity seq=7 host=post"]
        lines += (
            [f"[CONNECTIVITY] {outcome} channel=sensors-connectivity seq=7"]
            if outcome == "success"
            else [
                f"[CONNECTIVITY] failed channel=sensors-connectivity seq=7 "
                f"{outcome.removeprefix('failed ')}"
            ]
        )
    events = [parse_upload_event(line) for line in lines]
    assert all(event is not None for event in events)
    assert events[0].raw_fields["extension"] == "kept"
    assert events[0].as_event_payload()["raw_fields"]["reason"] == "health_get_failed"
    stats = _stats(*events)
    channel = stats.channel("connectivity")
    summary = channel.as_dict()
    report = check_upload_health(stats, connectivity=UploadChannelConfig(mode=mode))
    result = report.as_dict()["channels"]["connectivity"]
    assert summary["health_checks"] == result["health_checks"] == 3
    assert sum(result["health_check_reasons"].values()) == 3
    assert summary["attempts"] == result["attempts"] == int(outcome is not None)
    assert result["failures"] == int(outcome is not None and outcome != "success")
    assert result["successes"] == int(outcome == "success")
    assert result["success_rate"] == (
        None if outcome is None else float(outcome == "success")
    )
    assert result["max_consecutive_failures"] == result["failures"]
    assert result["warnings"] == 0
    assert summary["targets"] == (["post"] if outcome else [])
    assert channel.observed == (outcome is not None)
    assert report.status == ("ok" if outcome == "success" else missing_status)


@pytest.mark.parametrize(
    "reason",
    [
        "http_error",
        "begin_failed",
        "wifi_disconnected",
        "no_server_available",
        "encryption_failed",
        "health_unknown",
        "health_get_failed_extra",
        "health_get_failed",
        "health_begin_failed",
    ],
)
def test_non_health_failures_remain_delivery_failures(reason):
    event = parse_upload_event(
        f"[CONNECTIVITY] failed channel=sensors-connectivity seq=3 reason={reason}"
    )
    assert event is not None and not event.is_health_check
    channel = _stats(event).channel("connectivity")
    assert (
        channel.failures
        == channel.effective_attempts
        == channel.max_consecutive_failures
        == 1
    )
    assert channel.health_checks == 0


def test_health_does_not_reset_failure_streak_or_complete_pending_post():
    def event(tail):
        parsed = parse_upload_event(f"[CONNECTIVITY] {tail}")
        assert parsed is not None
        return parsed

    stats = _stats(
        event("failed channel=sensors-connectivity seq=1 reason=http_error"),
        event("attempt channel=sensors-connectivity seq=2"),
        event(
            "failed channel=sensors-connectivity seq=2 "
            "stage=health reason=health_get_failed"
        ),
        event("failed channel=sensors-connectivity seq=2 reason=http_error"),
        event("attempt channel=sensors-connectivity seq=2 encoding=proto"),
        event(
            "failed channel=sensors-connectivity seq=2 "
            "stage=health reason=health_begin_failed"
        ),
        event("attempt channel=sensors-connectivity seq=2 encoding=json"),
        event("success channel=sensors-connectivity seq=2"),
    )
    channel = stats.channel("connectivity")
    assert channel.failures == 2
    assert channel.successes == 1
    assert channel.success_rate == 1 / 3
    assert channel.max_consecutive_failures == 2
    assert channel.attempts == 3
    assert channel.warnings == 1
    assert channel.last_status == "success"


def test_health_reason_in_datalog_is_not_exempted():
    parsed = parse_upload_event("[DATALOG] failed reason=health_get_failed")
    assert parsed is not None and not parsed.is_health_check
    assert _stats(parsed).channel("datalog").failures == 1
