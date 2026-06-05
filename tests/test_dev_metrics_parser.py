from altruist_tester.parsers.dev_metrics import (
    DevMetrics,
    DevMetricsErrors,
    parse_dev_metrics_block,
    parse_dev_metrics_blocks,
)


def test_parse_full_urban_metrics_block():
    metrics = parse_dev_metrics_block(
        [
            "=== [URBAN] METRICS ===",
            "Status: ✓ ALIVE",
            "Uptime: 1h 2m 3s (3723s total)",
            "Boot: 1",
            "WiFi: ✓ OK (RSSI: -54 dBm)",
            "TX: 42 (last: 12s ago)",
            "Errors: WiFi=0 Sensor=1 SD=2",
            "ESP Temp: 43.1°C",
            "==========================",
        ]
    )

    assert metrics == DevMetrics(
        model="URBAN",
        status="ALIVE",
        uptime_sec=3723,
        boot=1,
        wifi_state="OK",
        rssi=-54,
        tx=42,
        last_tx_age_sec=12,
        errors=DevMetricsErrors(wifi=0, sensor=1, sd=2),
        esp_temp_c=43.1,
    )


def test_parse_metrics_block_without_unicode_status_symbols():
    metrics = parse_dev_metrics_block(
        [
            "=== [INSIGHT] METRICS ===",
            "Status: ? ERROR (WiFi Sensor)",
            "Uptime: 5m 7s (307s total)",
            "Boot: 12",
            "WiFi: ? DISCONNECTED",
            "TX: 0",
            "Errors: WiFi=3 Sensor=4 SD=0",
            "ESP Temp: 0.0°C",
        ]
    )

    assert metrics is not None
    assert metrics.model == "INSIGHT"
    assert metrics.status == "ERROR"
    assert metrics.uptime_sec == 307
    assert metrics.boot == 12
    assert metrics.wifi_state == "DISCONNECTED"
    assert metrics.rssi is None
    assert metrics.tx == 0
    assert metrics.last_tx_age_sec is None
    assert metrics.errors == DevMetricsErrors(wifi=3, sensor=4, sd=0)
    assert metrics.esp_temp_c == 0.0


def test_parse_partial_metrics_block_returns_available_fields():
    metrics = parse_dev_metrics_block(
        [
            "=== [URBAN] METRICS ===",
            "Status: ALIVE",
            "Boot: 7",
        ]
    )

    assert metrics == DevMetrics(model="URBAN", status="ALIVE", boot=7)


def test_parse_non_metrics_text_returns_none():
    assert parse_dev_metrics_block(["Boot: 1", "Status: ALIVE"]) is None


def test_metrics_event_payload_is_json_friendly():
    metrics = DevMetrics(
        model="URBAN",
        status="ALIVE",
        uptime_sec=121,
        errors=DevMetricsErrors(wifi=0, sensor=0, sd=0),
    )

    assert metrics.as_event_payload() == {
        "model": "URBAN",
        "status": "ALIVE",
        "uptime_sec": 121,
        "boot": None,
        "wifi_state": None,
        "rssi": None,
        "tx": None,
        "last_tx_age_sec": None,
        "errors": {"wifi": 0, "sensor": 0, "sd": 0},
        "esp_temp_c": None,
    }


def test_parse_dev_metrics_blocks_from_line_stream():
    metrics = parse_dev_metrics_blocks(
        [
            "noise before metrics",
            "=== [URBAN] METRICS ===",
            "Status: ✓ ALIVE",
            "Uptime: 2m 1s (121s total)",
            "Boot: 7",
            "==========================",
            "[INFO] unrelated line",
            "=== [URBAN] METRICS ===",
            "Status: ✓ ALIVE",
            "Uptime: 2m 4s (124s total)",
            "Boot: 7",
        ]
    )

    assert [item.uptime_sec for item in metrics] == [121, 124]
    assert [item.boot for item in metrics] == [7, 7]
