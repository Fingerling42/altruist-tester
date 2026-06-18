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


def test_detects_firmware_wifi_recovery_reboot():
    line = "[WiFi] STA link down too long; rebooting for recovery"

    assert [alert.code for alert in detect_keyword_alerts(line)] == [
        "WIFI_RECOVERY_REBOOT"
    ]


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
