from altruist_tester.parsers.dev_metrics import (
    DevMetrics,
    DevMetricsErrors,
    DevMetricsStreamParser,
    parse_dev_metrics_block,
    parse_dev_metrics_blocks,
)

HEALTH_LINE = (
    "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
    "errors=3 wifi=1 wifi_errors=1 sensor_errors=2 sd_errors=0 "
    "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
    "prev_uptime=0 prev_heap=0 last_section_id=0 last_section=Idle/MainLoop"
)


def test_parse_current_health_line():
    metrics = parse_dev_metrics_block([HEALTH_LINE])

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
    metrics = parse_dev_metrics_block(
        [
            "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
            "errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 "
            "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            "prev_uptime=0 prev_heap=0 last_section_id=0 "
            "last_section=Idle/MainLoop"
        ]
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
    metrics = parse_dev_metrics_block(
        [
            "[HEALTH] uptime=61 boot=5 heap=180000 rssi=-62 tx=0 "
            "errors=0 wifi=0 wifi_errors=0 sensor_errors=0 sd_errors=0 "
            "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            "prev_uptime=0 prev_heap=0 last_section_id=0 "
            "last_section=Idle/MainLoop"
        ]
    )

    assert metrics is not None
    assert metrics.wifi_state == "DISCONNECTED"


def test_parse_health_line_without_reset_context_keeps_base_metrics():
    metrics = parse_dev_metrics_block(
        [
            "[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
            "errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0"
        ]
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


def test_parse_metrics_block_is_not_supported():
    metrics = parse_dev_metrics_block(
        [
            "=== [URBAN] METRICS ===",
            "Status: ALIVE",
            "Uptime: 1h 2m 3s (3723s total)",
            "Boot: 1",
            "WiFi: OK (RSSI: -54 dBm)",
            "TX: 42",
            "Errors: WiFi=0 Sensor=1 SD=2",
            "ESP Temp: 43.1C",
            "==========================",
        ]
    )

    assert metrics is None


def test_parse_non_metrics_text_returns_none():
    assert parse_dev_metrics_block(["Boot: 1", "Status: ALIVE"]) is None


def test_metrics_event_payload_is_json_friendly():
    metrics = DevMetrics(
        status="ALIVE",
        uptime_sec=121,
        errors=DevMetricsErrors(wifi=0, sensor=0, sd=0),
    )

    assert metrics.as_event_payload() == {
        "model": None,
        "status": "ALIVE",
        "uptime_sec": 121,
        "boot": None,
        "wifi_state": None,
        "rssi": None,
        "tx": None,
        "last_tx_age_sec": None,
        "errors": {"wifi": 0, "sensor": 0, "sd": 0},
        "esp_temp_c": None,
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


def test_parse_dev_metrics_blocks_from_line_stream():
    metrics = parse_dev_metrics_blocks(
        [
            "noise before health",
            HEALTH_LINE,
            "[INFO] unrelated line",
            (
                "[HEALTH] uptime=3660 boot=4 heap=219000 rssi=-60 tx=13 "
                "errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 "
                "reset_reason=power_on_reset reset_code=1 crash_valid=0 "
                "prev_uptime=0 prev_heap=0 last_section_id=0 "
                "last_section=Idle/MainLoop"
            ),
        ]
    )

    assert [item.uptime_sec for item in metrics] == [3600, 3660]
    assert [item.boot for item in metrics] == [4, 4]


def test_dev_metrics_stream_parser_returns_current_health_line_immediately():
    parser = DevMetricsStreamParser()

    metrics = parser.feed(HEALTH_LINE)

    assert metrics is not None
    assert metrics.uptime_sec == 3600
    assert metrics.free_heap == 219584
    assert parser.finish() is None


def test_dev_metrics_stream_parser_ignores_legacy_blocks():
    parser = DevMetricsStreamParser()

    assert parser.feed("=== [URBAN] METRICS ===") is None
    assert parser.feed("Uptime: 2m 1s (121s total)") is None
    assert parser.feed("==========================") is None
    assert parser.finish() is None
