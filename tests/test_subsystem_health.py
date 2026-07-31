from altruist_tester.rules.subsystems import check_subsystem_health


def test_check_subsystem_health_passes_without_events():
    report = check_subsystem_health(())

    assert report.status == "ok"
    assert report.events_count == 0
    assert report.findings == ()


def test_check_subsystem_health_fails_on_error_events():
    report = check_subsystem_health(
        (
            {
                "ts": "2026-07-24T12:00:00.000Z",
                "level": "error",
                "subsystem": "sd",
                "reason": "open_append_failed",
            },
            {
                "ts": "2026-07-24T12:01:00.000Z",
                "level": "error",
                "subsystem": "sd",
                "reason": "open_append_failed",
            },
        )
    )

    assert report.status == "fail"
    assert report.events_count == 2
    assert report.failure_count == 1
    assert report.by_subsystem == {"sd": 2}
    assert report.by_reason == {"open_append_failed": 2}
    assert len(report.findings) == 1
    assert report.findings[0].status == "fail"
    assert report.findings[0].count == 2
    assert report.findings[0].first_seen == "2026-07-24T12:00:00.000Z"
    assert report.findings[0].last_seen == "2026-07-24T12:01:00.000Z"


def test_check_subsystem_health_warns_on_wifi_recovery_event():
    report = check_subsystem_health(
        (
            {
                "ts": "2026-07-24T12:00:00.000Z",
                "level": "event",
                "subsystem": "wifi",
                "reason": "sta_recovery",
            },
        )
    )

    assert report.status == "warn"
    assert report.warning_count == 1
    assert report.findings[0].status == "warn"


def test_check_subsystem_health_fails_on_wifi_recovery_reboot_event():
    report = check_subsystem_health(
        (
            {
                "ts": "2026-07-24T12:00:00.000Z",
                "level": "event",
                "subsystem": "wifi",
                "reason": "sta_recovery_reboot",
            },
        )
    )

    assert report.status == "fail"
    assert report.failure_count == 1
    assert report.findings[0].status == "fail"


def test_check_subsystem_health_warns_on_display_stuck():
    report = check_subsystem_health(
        (
            {
                "ts": "2026-07-24T12:00:00.000Z",
                "level": "error",
                "subsystem": "display",
                "reason": "epd_stuck",
            },
        )
    )

    assert report.status == "warn"
    assert report.findings[0].status == "warn"
