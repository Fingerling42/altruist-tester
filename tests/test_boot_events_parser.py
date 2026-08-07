from altruist_tester.parsers.boot_events import BootEvent, parse_boot_event


def test_parse_current_boot_event():
    event = parse_boot_event(
        "[BOOT] reset_reason=usb_reset_flash_boot reset_code=11 boot=30 "
        "crash_valid=1 prev_uptime=11 prev_heap=227720 "
        "last_section_id=0 last_section=Idle/MainLoop heap=192072"
    )

    assert event == BootEvent(
        reset_reason="usb_reset_flash_boot",
        reset_code=11,
        boot=30,
        crash_valid=True,
        prev_uptime_sec=11,
        prev_free_heap=227720,
        last_section_id=0,
        last_section="Idle/MainLoop",
        free_heap=192072,
        raw_fields={
            "reset_reason": "usb_reset_flash_boot",
            "reset_code": "11",
            "boot": "30",
            "crash_valid": "1",
            "prev_uptime": "11",
            "prev_heap": "227720",
            "last_section_id": "0",
            "last_section": "Idle/MainLoop",
            "heap": "192072",
        },
    )


def test_parse_boot_event_rejects_unrelated_lines():
    assert parse_boot_event("[HEALTH] uptime=1 boot=1") is None
    assert parse_boot_event("[BOOT] no key value fields") is None


def test_boot_event_payload_is_json_friendly():
    event = BootEvent(
        reset_reason="power_on_reset",
        reset_code=1,
        boot=4,
        crash_valid=False,
        prev_uptime_sec=0,
        prev_free_heap=0,
        last_section_id=0,
        last_section="Idle/MainLoop",
        free_heap=219584,
    )

    assert event.as_event_payload() == {
        "reset_reason": "power_on_reset",
        "reset_code": 1,
        "boot": 4,
        "crash_valid": False,
        "prev_uptime_sec": 0,
        "prev_free_heap": 0,
        "last_section_id": 0,
        "last_section": "Idle/MainLoop",
        "free_heap": 219584,
        "raw_fields": {},
    }
