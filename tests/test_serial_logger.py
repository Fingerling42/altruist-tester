import json
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester.artifacts import create_run_artifacts
from altruist_tester.serial_logger import capture_raw_serial


class FakeSerial:
    def __init__(self, lines, clock):
        self._lines = list(lines)
        self._clock = clock

    def readline(self):
        self._clock.advance(0.25)
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeClock:
    def __init__(self):
        self.current = 0.0

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def test_capture_raw_serial_writes_raw_log_without_line_events_by_default(tmp_path):
    clock = FakeClock()
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="1s",
        duration_seconds=1,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )
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
    assert artifacts.serial_log.read_bytes() == (
        b"first line\r\nsecond line\nbad utf8: \xff\n"
    )

    events = [
        json.loads(line)
        for line in artifacts.events_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert serial_events == []


def test_capture_raw_serial_can_mirror_lines_to_events(tmp_path):
    clock = FakeClock()
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="1s",
        duration_seconds=1,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )
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

    events = [
        json.loads(line)
        for line in artifacts.events_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert [event["line"] for event in serial_events] == [
        "first line",
        "second line",
        "bad utf8: �",
    ]


def test_capture_raw_serial_writes_dev_metrics_events(tmp_path):
    clock = FakeClock()
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="5s",
        duration_seconds=5,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
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

    events = [
        json.loads(line)
        for line in artifacts.events_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    metrics_events = [event for event in events if event["type"] == "dev_metrics"]
    assert len(metrics_events) == 2
    assert metrics_events[0]["uptime_sec"] == 121
    assert metrics_events[0]["errors"] == {"wifi": 0, "sensor": 1, "sd": 2}
    assert metrics_events[1]["uptime_sec"] == 124
    assert metrics_events[1]["rssi"] == -79

    assert stats.dev_metrics.count == 2
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
