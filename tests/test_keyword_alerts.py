from altruist_tester.parsers.keyword_alerts import detect_keyword_alerts


def test_detects_runtime_failure_keywords_case_insensitively():
    alerts = detect_keyword_alerts("Guru Meditation Error: Core 0 panic'ed")

    assert [alert.code for alert in alerts] == ["PANIC", "GURU_MEDITATION"]
    assert all(alert.severity == "fail" for alert in alerts)
    assert alerts[0].line == "Guru Meditation Error: Core 0 panic'ed"


def test_detects_watchdog_and_brownout_keywords():
    assert [alert.code for alert in detect_keyword_alerts("Task watchdog timeout")] == [
        "WATCHDOG"
    ]
    assert [
        alert.code for alert in detect_keyword_alerts("Brownout detector was triggered")
    ] == ["BROWNOUT"]
    assert [
        alert.code for alert in detect_keyword_alerts("Brownout (voltage too low)")
    ] == ["BROWNOUT"]


def test_detects_firmware_watchdog_reboot_keywords():
    line = "[Watchdog] Datalog send timeout; rebooting"

    assert [alert.code for alert in detect_keyword_alerts(line)] == ["WATCHDOG"]


def test_detects_esp_boot_watchdog_reset_reason():
    assert [
        alert.code for alert in detect_keyword_alerts("rst:0x10 (RTCWDT_RTC_RESET)")
    ] == ["WATCHDOG"]


def test_detects_abnormal_firmware_reset_reasons():
    lines = [
        "Unexpected restart (recovered)",
        "Power glitch",
        "CPU lock-up",
        "eFuse error",
    ]

    assert [detect_keyword_alerts(line)[0].code for line in lines] == [
        "UNEXPECTED_RESTART",
        "POWER_GLITCH",
        "CPU_LOCKUP",
        "EFUSE_ERROR",
    ]


def test_detects_structured_wifi_recovery_events():
    alerts = detect_keyword_alerts(
        "[SUBSYSTEM] event subsystem=wifi reason=sta_recovery "
        "mode=deep status=6 ip=0.0.0.0"
    )

    assert [alert.code for alert in alerts] == ["WIFI_RECOVERY"]
    assert alerts[0].severity == "warn"


def test_detects_structured_wifi_recovery_reboot():
    alerts = detect_keyword_alerts(
        "[SUBSYSTEM] event subsystem=wifi reason=sta_recovery_reboot down_ms=900000"
    )

    assert [alert.code for alert in alerts] == ["WIFI_RECOVERY_REBOOT"]
    assert alerts[0].severity == "fail"


def test_detects_wifi_config_timeout():
    line = "[SUBSYSTEM] error subsystem=wifi reason=config_timeout"

    assert [alert.code for alert in detect_keyword_alerts(line)] == [
        "WIFI_CONFIG_TIMEOUT"
    ]


def test_detects_structured_subsystem_errors():
    lines = [
        "[SUBSYSTEM] error subsystem=sd reason=open_append_failed path=/data/x.csv",
        "[SUBSYSTEM] error subsystem=config reason=json_parse_failed path=/config.json",
        "[SUBSYSTEM] error subsystem=ota reason=http_get_failed host=example code=404",
        "[SUBSYSTEM] error subsystem=display reason=epd_stuck action=recover",
        "[SUBSYSTEM] error subsystem=sensor reason=json_overflow sensor=BME680",
        "[SUBSYSTEM] error subsystem=api reason=json_snapshot_overflow memory=4096",
    ]

    assert [detect_keyword_alerts(line)[0].code for line in lines] == [
        "SD_ERROR",
        "CONFIG_ERROR",
        "OTA_ERROR",
        "DISPLAY_STUCK",
        "SENSOR_JSON_OVERFLOW",
        "API_JSON_OVERFLOW",
    ]


def test_ignores_human_readable_subsystem_error_lines():
    lines = [
        "[SDCardLogger] SD card NOT connected",
        "Card Mount Failed",
        "failed to mount FS",
        "OTA failed after all attempts",
        "[EPD] Display stuck detected - recovering and retrying with FULL refresh",
        "[Sensors] JSON overflow after fetch",
        "[API] JSON snapshot overflow; skipping send",
    ]

    assert [detect_keyword_alerts(line) for line in lines] == [[] for _ in lines]


def test_detects_access_faults_and_assertions():
    lines = [
        "assert failed: tcpip_send_msg_wait_sem",
        "Stack canary watchpoint triggered",
        "heap corruption detected",
        "Load access fault",
        "Store access fault",
    ]

    assert [detect_keyword_alerts(line)[0].code for line in lines] == [
        "ASSERT_FAILED",
        "STACK_CANARY",
        "HEAP_CORRUPTION",
        "LOAD_ACCESS_FAULT",
        "STORE_ACCESS_FAULT",
    ]


def test_ignores_normal_firmware_reset_reasons():
    lines = [
        "Power-on reset",
        "External reset",
        "Software reset",
        "Wake from deep sleep",
        "Reset over SDIO",
        "USB reset (flash/boot)",
        "JTAG reset",
        "OTA firmware update",
        "Configuration saved",
        "User restart (web)",
    ]

    assert [detect_keyword_alerts(line) for line in lines] == [[] for _ in lines]


def test_ignores_failure_injection_metadata_lines():
    assert (
        detect_keyword_alerts("[TEST] Injecting serial sample: keyword-watchdog") == []
    )
    assert (
        detect_keyword_alerts("[TEST] Injecting serial sample: keyword-brownout") == []
    )


def test_ignores_expected_network_error_lines():
    assert (
        detect_keyword_alerts("[ERROR] [Map] FAILED: server returned HTTP error") == []
    )
    assert detect_keyword_alerts("[Datalog] FAILED: account balance too low") == []
    assert (
        detect_keyword_alerts(
            "[CONNECTIVITY] failed channel=sensors-connectivity seq=3 "
            "reason=http_error host=connectivity.robonomics.network code=500"
        )
        == []
    )
    assert (
        detect_keyword_alerts(
            "[DATALOG] failed reason=rpc_error code=1010 "
            "message=invalid_transaction response_len=111"
        )
        == []
    )
