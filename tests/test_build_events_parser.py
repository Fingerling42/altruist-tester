from altruist_tester.parsers.build_events import BuildEvent, parse_build_event


def test_parse_current_build_event():
    event = parse_build_event(
        "[BUILD] version=R-URB_2026-07-08-testing+abc1234 "
        "channel=testing commit=abc1234 model=urban target=esp32c6 "
        "language=en profile=release"
    )

    assert event == BuildEvent(
        version="R-URB_2026-07-08-testing+abc1234",
        channel="testing",
        commit="abc1234",
        model="urban",
        target="esp32c6",
        language="en",
        profile="release",
        raw_fields={
            "version": "R-URB_2026-07-08-testing+abc1234",
            "channel": "testing",
            "commit": "abc1234",
            "model": "urban",
            "target": "esp32c6",
            "language": "en",
            "profile": "release",
        },
    )


def test_parse_build_event_rejects_unrelated_lines():
    assert parse_build_event("[BOOT] reset_reason=power_on_reset") is None
    assert parse_build_event("[BUILD] no key value fields") is None


def test_build_event_payload_is_json_friendly():
    event = BuildEvent(
        version="R-INS_2026-07-08",
        channel="stable",
        commit="unknown",
        model="insight",
        target="esp32c6",
        language="ru",
        profile="debug",
    )

    assert event.as_event_payload() == {
        "version": "R-INS_2026-07-08",
        "channel": "stable",
        "commit": "unknown",
        "model": "insight",
        "target": "esp32c6",
        "language": "ru",
        "profile": "debug",
        "raw_fields": {},
    }
