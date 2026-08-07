from altruist_tester.parsers.subsystem_events import (
    SubsystemEvent,
    parse_subsystem_event,
)


def test_parse_subsystem_error_event():
    event = parse_subsystem_event(
        "[SUBSYSTEM] error subsystem=sd reason=open_append_failed "
        "path=/data/SDS011/2026-07-24.csv"
    )

    assert event == SubsystemEvent(
        level="error",
        subsystem="sd",
        reason="open_append_failed",
        details={"path": "/data/SDS011/2026-07-24.csv"},
        line=(
            "[SUBSYSTEM] error subsystem=sd reason=open_append_failed "
            "path=/data/SDS011/2026-07-24.csv"
        ),
    )


def test_parse_subsystem_event_line():
    event = parse_subsystem_event(
        "[SUBSYSTEM] event subsystem=wifi reason=sta_recovery "
        "mode=deep status=6 ip=0.0.0.0"
    )

    assert event is not None
    assert event.level == "event"
    assert event.subsystem == "wifi"
    assert event.reason == "sta_recovery"
    assert event.details == {
        "mode": "deep",
        "status": "6",
        "ip": "0.0.0.0",
    }


def test_parse_subsystem_event_requires_subsystem_and_reason():
    assert parse_subsystem_event("[SUBSYSTEM] error reason=open_append_failed") is None
    assert parse_subsystem_event("[SUBSYSTEM] error subsystem=sd") is None
    assert (
        parse_subsystem_event("[ERROR] subsystem=sd reason=open_append_failed") is None
    )


def test_subsystem_event_payload_is_json_friendly():
    event = SubsystemEvent(
        level="error",
        subsystem="sensor",
        reason="json_overflow",
        details={"sensor": "BME680"},
        line="[SUBSYSTEM] error subsystem=sensor reason=json_overflow sensor=BME680",
    )

    assert event.as_event_payload() == {
        "level": "error",
        "subsystem": "sensor",
        "reason": "json_overflow",
        "details": {"sensor": "BME680"},
        "line": "[SUBSYSTEM] error subsystem=sensor reason=json_overflow sensor=BME680",
    }
