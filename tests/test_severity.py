from altruist_tester.rules.severity import (
    severity_for_keyword_alert,
    severity_for_missing_boot_context,
    severity_for_missing_health_telemetry,
    severity_for_missing_sensor_metrics,
    severity_for_subsystem_event,
    severity_for_upload_mode,
)


def test_crash_and_reset_keywords_are_failures():
    assert severity_for_keyword_alert("WATCHDOG") == "fail"
    assert severity_for_keyword_alert("BROWNOUT") == "fail"


def test_missing_sensor_metrics_are_failures():
    assert severity_for_missing_sensor_metrics() == "fail"


def test_missing_release_log_context_has_explicit_severity():
    assert severity_for_missing_health_telemetry() == "fail"
    assert severity_for_missing_boot_context() == "warn"


def test_upload_severity_depends_on_channel_mode():
    assert severity_for_upload_mode("disabled") == "warn"
    assert severity_for_upload_mode("optional") == "warn"
    assert severity_for_upload_mode("required") == "fail"


def test_subsystem_severity_mapping_marks_critical_errors_as_failures():
    assert (
        severity_for_subsystem_event(
            level="error",
            subsystem="sensor",
            reason="json_overflow",
        )
        == "fail"
    )
    assert (
        severity_for_subsystem_event(
            level="error",
            subsystem="sd",
            reason="open_append_failed",
        )
        == "fail"
    )
    assert (
        severity_for_subsystem_event(
            level="error",
            subsystem="ota",
            reason="update_failed",
        )
        == "fail"
    )


def test_subsystem_severity_mapping_keeps_recoverable_events_as_warnings():
    assert (
        severity_for_subsystem_event(
            level="event",
            subsystem="wifi",
            reason="sta_recovery",
        )
        == "warn"
    )
    assert (
        severity_for_subsystem_event(
            level="error",
            subsystem="display",
            reason="busy_timeout",
        )
        == "warn"
    )


def test_subsystem_severity_mapping_fails_recovery_reboots():
    assert (
        severity_for_subsystem_event(
            level="event",
            subsystem="wifi",
            reason="sta_recovery_reboot",
        )
        == "fail"
    )
