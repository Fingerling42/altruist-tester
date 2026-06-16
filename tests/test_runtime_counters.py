from altruist_tester.rules.runtime import check_runtime_counters


def test_check_runtime_counters_passes_for_monotonic_values():
    report = check_runtime_counters(
        [
            {"boot": 7, "uptime_sec": 10},
            {"boot": 7, "uptime_sec": 20},
            {"boot": 7, "uptime_sec": 30},
        ]
    )

    assert report.status == "ok"
    assert report.ok is True
    assert report.initial_boot == 7
    assert report.last_boot == 7
    assert report.min_uptime_sec == 10
    assert report.max_uptime_sec == 30
    assert report.failure_count == 0
    assert "initial boot counter was 7" in report.message


def test_check_runtime_counters_fails_when_uptime_decreases():
    report = check_runtime_counters(
        [
            {"boot": 1, "uptime_sec": 10},
            {"boot": 1, "uptime_sec": 20},
            {"boot": 1, "uptime_sec": 5},
        ]
    )

    assert report.status == "fail"
    assert report.ok is False
    assert report.failure_count == 1
    assert report.findings[0].code == "UPTIME_DECREASED"
    assert report.findings[0].previous_value == 20
    assert report.findings[0].current_value == 5


def test_check_runtime_counters_fails_when_boot_counter_increases():
    report = check_runtime_counters(
        [
            {"boot": 1, "uptime_sec": 10},
            {"boot": 1, "uptime_sec": 20},
            {"boot": 2, "uptime_sec": 30},
        ]
    )

    assert report.status == "fail"
    assert report.failure_count == 1
    assert report.findings[0].code == "BOOT_COUNTER_INCREASED"
    assert report.findings[0].previous_value == 1
    assert report.findings[0].current_value == 2


def test_check_runtime_counters_warns_without_metrics():
    report = check_runtime_counters([])

    assert report.status == "warn"
    assert report.ok is True
    assert report.checked_records_count == 0
    assert report.initial_boot is None
    assert report.findings == ()
