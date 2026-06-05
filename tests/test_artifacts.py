import json
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester.artifacts import (
    create_run_artifacts,
    device_hint_from_port,
    format_timestamp,
)


def test_device_hint_from_port():
    assert device_hint_from_port(Path("/dev/ttyACM0")) == "ttyACM0"
    assert (
        device_hint_from_port(Path("/dev/serial/by-id/usb Test Device"))
        == "usb-Test-Device"
    )


def test_format_timestamp_uses_utc_z_suffix():
    value = datetime(2026, 6, 5, 12, 30, 1, 123000, tzinfo=UTC)

    assert format_timestamp(value) == "2026-06-05T12:30:01.123Z"


def test_create_run_artifacts_initializes_files(tmp_path):
    started_at = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)

    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="10m",
        duration_seconds=600,
        started_at=started_at,
    )

    assert artifacts.run_id == "2026-06-05T12-00-00Z_ttyACM0"
    assert artifacts.serial_log.exists()
    assert artifacts.events_jsonl.exists()
    assert artifacts.samples_jsonl.exists()
    assert artifacts.summary_json.exists()
    assert artifacts.report_txt.exists()

    summary = json.loads(artifacts.summary_json.read_text())
    assert summary["status"] == "running"
    assert summary["port"] == "/dev/ttyACM0"
    assert summary["duration_sec"] == 600

    event = json.loads(artifacts.events_jsonl.read_text().splitlines()[0])
    assert event["type"] == "run_started"
    assert event["port"] == "/dev/ttyACM0"
