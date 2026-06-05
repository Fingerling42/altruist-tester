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


def test_capture_raw_serial_writes_raw_log_and_events(tmp_path):
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
    assert [event["line"] for event in serial_events] == [
        "first line",
        "second line",
        "bad utf8: �",
    ]
