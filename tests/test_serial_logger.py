import json
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester.artifacts import create_run_artifacts
from altruist_tester.serial_logger import capture_raw_serial

# Fixed artifact timestamps keep JSON/report assertions stable.
STARTED_AT = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)


class FakeSerial:
    def __init__(self, lines, clock):
        self._lines = list(lines)
        self._clock = clock

    def readline(self):
        # Advance on every read so interline gap and silence calculations are
        # deterministic without sleeping in tests.
        self._clock.advance(0.25)
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeClock:
    """Small monotonic-clock replacement controlled by the fake serial port."""

    def __init__(self):
        self.current = 0.0

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def _create_artifacts(tmp_path, *, duration_input="1s", duration_seconds=1):
    return create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input=duration_input,
        duration_seconds=duration_seconds,
        started_at=STARTED_AT,
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_capture_raw_serial_writes_raw_log_without_line_events_by_default(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"first line\r\n",
            b"second line\n",
            b"bad utf8: \xff\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    assert stats.lines_read == 3
    assert stats.bytes_read == 36
    assert stats.first_line_elapsed_seconds == 0.25
    assert stats.last_line_elapsed_seconds == 0.75
    assert stats.max_interline_gap_seconds == 0.25
    assert artifacts.serial_log.read_bytes() == (
        b"first line\r\nsecond line\nbad utf8: \xff\n"
    )

    events = _read_jsonl(artifacts.events_jsonl)
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert serial_events == []


def test_capture_raw_serial_can_mirror_lines_to_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"first line\r\n",
            b"second line\n",
            b"bad utf8: \xff\n",
        ],
        clock,
    )

    stats = capture_raw_serial(
        serial,
        artifacts,
        1,
        clock=clock,
        mirror_lines_to_events=True,
    )

    assert stats.lines_read == 3
    assert stats.bytes_read == 36

    events = _read_jsonl(artifacts.events_jsonl)
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert [event["line"] for event in serial_events] == [
        "first line",
        "second line",
        "bad utf8: �",
    ]


def test_capture_raw_serial_writes_keyword_alert_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"normal boot line\n",
            b"Guru Meditation Error: Core 0 panic'ed\n",
            b"[ERROR] [Map] FAILED: WiFi disconnected\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    alert_events = [event for event in events if event["type"] == "keyword_alert"]
    assert [event["code"] for event in alert_events] == [
        "PANIC",
        "GURU_MEDITATION",
    ]
    assert stats.keyword_alerts_count == 2
    assert [alert["code"] for alert in stats.keyword_alerts] == [
        "PANIC",
        "GURU_MEDITATION",
    ]


def test_capture_raw_serial_writes_upload_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path, duration_input="2s", duration_seconds=2)
    serial = FakeSerial(
        [
            b"[123] [INFO] [Map#7] Send attempt\n",
            b"[124] [INFO] [Map#7] POST to connectivity.robonomics.network:65/\n",
            (
                b"[125] [INFO] [Map#7] OK, POST succeeded -> "
                b"connectivity.robonomics.network\n"
            ),
            b"[Datalog] Sending: h:45,t:24\n",
            b"[Datalog] FAILED\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 2, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    upload_events = [event for event in events if event["type"] == "upload_event"]
    assert [event["status"] for event in upload_events] == [
        "attempt",
        "target",
        "success",
        "attempt",
        "failure",
    ]
    assert stats.upload_stats.channel("connectivity").attempts == 1
    assert stats.upload_stats.channel("connectivity").successes == 1
    assert stats.upload_stats.channel("datalog").failures == 1


def test_capture_raw_serial_writes_sensor_samples(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            (
                b'{"service_data":{"signal_strength":-38},'
                b'"BME280":{"temperature":{"value":25.5,"units":"C"}},'
                b'"SDS":{"P1":{"value":16.3,"units":"ppm"}}}\n'
            ),
            b"[123] [INFO] Datalog data: : h:65.99,t:25.51,p1:16.33\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    samples = _read_jsonl(artifacts.samples_jsonl)
    assert [
        (sample["sensor"], sample["metric"], sample["value"]) for sample in samples
    ] == [
        ("BME280", "temperature", 25.5),
        ("SDS", "P1", 16.3),
        ("datalog", "humidity", 65.99),
        ("datalog", "temperature", 25.51),
        ("datalog", "P1", 16.33),
    ]
    assert stats.sensor_samples_count == 5
    assert stats.sensor_series.latest(("BME280", "temperature")).value == 25.5
    assert stats.sensor_series.latest(("datalog", "humidity")).value == 65.99


def test_capture_raw_serial_reports_progress(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="2s",
        duration_seconds=2,
    )
    serial = FakeSerial(
        [
            b'{"BME280":{"temperature":{"value":25.5,"units":"C"}}}\n',
            b"Guru Meditation Error: Core 0 panic'ed\n",
            b"=== [URBAN] METRICS ===\n",
            b"Status: ALIVE\n",
            b"==========================\n",
        ],
        clock,
    )
    progress_updates = []

    capture_raw_serial(
        serial,
        artifacts,
        2,
        clock=clock,
        progress_callback=progress_updates.append,
        progress_interval_seconds=0.5,
    )

    assert progress_updates
    # The logger always emits a final complete update even when the interval
    # callback cadence does not land exactly on the run duration.
    assert progress_updates[-1].complete is True
    assert progress_updates[-1].lines_read == 5
    assert progress_updates[-1].dev_metrics_count == 1
    assert progress_updates[-1].keyword_alerts_count == 2
    assert progress_updates[-1].sensor_samples_count == 1
    assert progress_updates[-1].percent == 100.0


def test_capture_raw_serial_writes_dev_metrics_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="5s",
        duration_seconds=5,
    )
    serial = FakeSerial(
        [
            b"=== [URBAN] METRICS ===\n",
            b"Status: \xe2\x9c\x93 ALIVE\n",
            b"Uptime: 2m 1s (121s total)\n",
            b"Boot: 7\n",
            b"WiFi: \xe2\x9c\x93 OK (RSSI: -81 dBm)\n",
            b"TX: 1 (last: 65s ago)\n",
            b"Errors: WiFi=0 Sensor=1 SD=2\n",
            b"ESP Temp: 35.6\xc2\xb0C\n",
            b"==========================\n",
            b"=== [URBAN] METRICS ===\n",
            b"Status: \xe2\x9c\x93 ALIVE\n",
            b"Uptime: 2m 4s (124s total)\n",
            b"Boot: 7\n",
            b"WiFi: \xe2\x9c\x93 OK (RSSI: -79 dBm)\n",
            b"TX: 2 (last: 1s ago)\n",
            b"Errors: WiFi=3 Sensor=0 SD=1\n",
            b"ESP Temp: 36.1\xc2\xb0C\n",
            b"==========================\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 5, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    metrics_events = [event for event in events if event["type"] == "dev_metrics"]
    assert len(metrics_events) == 2
    assert metrics_events[0]["uptime_sec"] == 121
    assert metrics_events[0]["errors"] == {"wifi": 0, "sensor": 1, "sd": 2}
    assert metrics_events[1]["uptime_sec"] == 124
    assert metrics_events[1]["rssi"] == -79

    # Check both the persisted health event shape and the aggregate summary used
    # later by runtime-counter rules.
    assert stats.dev_metrics.count == 2
    assert [record["uptime_sec"] for record in stats.dev_metrics_records] == [121, 124]
    assert [record["boot"] for record in stats.dev_metrics_records] == [7, 7]
    assert stats.dev_metrics_records[0]["ts"] == metrics_events[0]["ts"]
    assert stats.dev_metrics.first_seen == metrics_events[0]["ts"]
    assert stats.dev_metrics.last_seen == metrics_events[1]["ts"]
    assert stats.dev_metrics.max_boot == 7
    assert stats.dev_metrics.min_uptime_sec == 121
    assert stats.dev_metrics.max_uptime_sec == 124
    assert stats.dev_metrics.min_rssi == -81
    assert stats.dev_metrics.max_rssi == -79
    assert stats.dev_metrics.min_esp_temp_c == 35.6
    assert stats.dev_metrics.max_esp_temp_c == 36.1
    assert stats.dev_metrics.max_errors == {"wifi": 3, "sensor": 1, "sd": 2}
    assert stats.dev_metrics.last_metrics == {
        "model": "URBAN",
        "status": "ALIVE",
        "uptime_sec": 124,
        "boot": 7,
        "wifi_state": "OK",
        "rssi": -79,
        "tx": 2,
        "last_tx_age_sec": 1,
        "errors": {"wifi": 3, "sensor": 0, "sd": 1},
        "esp_temp_c": 36.1,
    }
