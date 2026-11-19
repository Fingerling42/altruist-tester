import pytest

from altruist_tester.parsers.dev_metrics import (
    DevMetrics,
    DevMetricsErrors,
    parse_health_line,
)

HEALTH_LINE = (
    "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
    "errors=3 wifi=1 wifi_errors=1 sensor_errors=2 sd_errors=0 "
    "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
    "prev_uptime=0 prev_heap=0 last_section_id=0 last_section=Idle/MainLoop"
)


def test_parse_current_health_line():
    metrics = parse_health_line(HEALTH_LINE)

    assert metrics == DevMetrics(
        status="ERROR",
        uptime_sec=3600,
        boot=4,
        wifi_state="OK",
        rssi=-62,
        tx=12,
        errors=DevMetricsErrors(wifi=1, sensor=2, sd=0),
        free_heap=219584,
        error_count=3,
        reset_reason="power_on_reset",
        reset_code=1,
        crash_valid=False,
        prev_uptime_sec=0,
        prev_free_heap=0,
        last_section_id=0,
        last_section="Idle/MainLoop",
    )


def test_parse_current_health_line_with_reset_context():
    metrics = parse_health_line(
        "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
        "errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 "
        "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
        "prev_uptime=0 prev_heap=0 last_section_id=0 "
        "last_section=Idle/MainLoop"
    )

    assert metrics is not None
    assert metrics.reset_reason == "power_on_reset"
    assert metrics.reset_code == 1
    assert metrics.crash_valid is False
    assert metrics.prev_uptime_sec == 0
    assert metrics.prev_free_heap == 0
    assert metrics.last_section_id == 0
    assert metrics.last_section == "Idle/MainLoop"


def test_parse_current_health_line_uses_wifi_field_for_link_state():
    metrics = parse_health_line(
        "[HEALTH] uptime=61 boot=5 heap=180000 rssi=-62 tx=0 "
        "errors=0 wifi=0 wifi_errors=0 sensor_errors=0 sd_errors=0 "
        "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
        "prev_uptime=0 prev_heap=0 last_section_id=0 "
        "last_section=Idle/MainLoop"
    )

    assert metrics is not None
    assert metrics.wifi_state == "DISCONNECTED"


def test_parse_health_line_without_reset_context_keeps_base_metrics():
    metrics = parse_health_line(
        "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
        "errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0"
    )

    assert metrics is not None
    assert metrics.status == "ALIVE"
    assert metrics.uptime_sec == 3600
    assert metrics.boot == 4
    assert metrics.free_heap == 219584
    assert metrics.rssi == -62
    assert metrics.tx == 12
    assert metrics.errors == DevMetricsErrors(wifi=0, sensor=0, sd=0)
    assert metrics.reset_reason is None
    assert metrics.reset_code is None
    assert metrics.crash_valid is None
    assert metrics.prev_uptime_sec is None
    assert metrics.prev_free_heap is None
    assert metrics.last_section_id is None
    assert metrics.last_section is None


@pytest.mark.parametrize(
    "line",
    [
        "",
        "Boot: 1",
        "Status: ALIVE",
        "=== [URBAN] METRICS ===",
        "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 errors=0",
        HEALTH_LINE.replace("wifi=1", "wifi=2"),
        HEALTH_LINE.replace("uptime=3600", "uptime=invalid"),
        HEALTH_LINE.replace("sd_errors=0", "sd_errors=-1"),
        HEALTH_LINE + "\n" + HEALTH_LINE,
    ],
)
def test_invalid_or_incomplete_health_line_returns_none(line):
    assert parse_health_line(line) is None


def test_invalid_optional_context_keeps_base_metrics():
    metrics = parse_health_line(
        HEALTH_LINE.replace("reset_code=1", "reset_code=invalid")
        .replace("crash_valid=0", "crash_valid=invalid")
        .replace("prev_uptime=0", "prev_uptime=invalid")
        .replace("prev_heap=0", "prev_heap=invalid")
        .replace("last_section_id=0", "last_section_id=invalid")
    )
    assert metrics is not None
    assert metrics.uptime_sec == 3600
    assert metrics.reset_code is None
    assert metrics.crash_valid is None
    assert metrics.prev_uptime_sec is None
    assert metrics.prev_free_heap is None
    assert metrics.last_section_id is None


def test_metrics_event_payload_is_json_friendly():
    metrics = DevMetrics(
        status="ALIVE",
        uptime_sec=121,
        errors=DevMetricsErrors(wifi=0, sensor=0, sd=0),
    )

    assert metrics.as_event_payload() == {
        "status": "ALIVE",
        "uptime_sec": 121,
        "boot": None,
        "wifi_state": None,
        "rssi": None,
        "tx": None,
        "errors": {"wifi": 0, "sensor": 0, "sd": 0},
        "free_heap": None,
        "error_count": None,
        "reset_reason": None,
        "reset_code": None,
        "crash_valid": None,
        "prev_uptime_sec": None,
        "prev_free_heap": None,
        "last_section_id": None,
        "last_section": None,
    }


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_health_line_accepts_serial_line_endings(ending):
    assert parse_health_line("  " + HEALTH_LINE + ending) == parse_health_line(
        HEALTH_LINE
    )
