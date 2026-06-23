import json
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester.artifacts import (
    create_batch_artifacts,
    create_run_artifacts,
    device_hint_from_port,
    format_timestamp,
    safe_artifact_name,
)
from altruist_tester.config import BatchConfig, BatchDeviceConfig
from altruist_tester.samples import SensorSample


def test_device_hint_from_port():
    assert device_hint_from_port(Path("/dev/ttyACM0")) == "ttyACM0"
    assert (
        device_hint_from_port(Path("/dev/serial/by-id/usb Test Device"))
        == "usb-Test-Device"
    )


def test_safe_artifact_name():
    assert safe_artifact_name("slot-01") == "slot-01"
    assert safe_artifact_name("rack 1 / slot 02") == "rack-1-slot-02"
    assert safe_artifact_name("...") == "unknown"


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


def test_create_batch_artifacts_initializes_batch_skeleton(tmp_path):
    started_at = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    config = BatchConfig(
        duration_input="24h",
        duration_seconds=24 * 60 * 60,
        baud=115200,
        output_dir=tmp_path,
        devices=(
            BatchDeviceConfig(
                slot="slot-01",
                port=Path("/dev/serial/by-path/urban"),
                model="urban",
                config=Path("configs/urban.example.toml"),
                effective_config=Path("configs/urban.example.toml"),
            ),
            BatchDeviceConfig(
                slot="rack 1 / slot 02",
                port=Path("/dev/serial/by-path/insight"),
                model="insight",
                config=Path("configs/insight.example.toml"),
                effective_config=Path("configs/insight.example.toml"),
            ),
        ),
    )

    artifacts = create_batch_artifacts(tmp_path, config=config, started_at=started_at)

    assert artifacts.batch_id == "batch_2026-06-05T12-00-00Z"
    assert artifacts.batch_dir == tmp_path / "batch_2026-06-05T12-00-00Z"
    assert artifacts.summary_json.exists()
    assert artifacts.report_txt.exists()
    assert artifacts.devices_dir == artifacts.batch_dir / "devices"
    assert artifacts.devices[0].output_dir == artifacts.devices_dir / "slot-01"
    assert artifacts.devices[1].output_dir == artifacts.devices_dir / "rack-1-slot-02"
    assert artifacts.devices[0].output_dir.exists()
    assert artifacts.devices[1].output_dir.exists()
    assert not (artifacts.devices[0].output_dir / "serial.log").exists()

    summary = json.loads(artifacts.summary_json.read_text(encoding="utf-8"))
    assert summary["status"] == "initialized"
    assert summary["duration_sec"] == 24 * 60 * 60
    assert summary["devices"][0]["slot"] == "slot-01"
    assert summary["devices"][0]["slot_dir"] == str(artifacts.devices[0].output_dir)
    assert summary["devices"][0]["config"] == "configs/urban.example.toml"
    assert summary["devices"][1]["slot"] == "rack 1 / slot 02"

    report = artifacts.report_txt.read_text(encoding="utf-8")
    assert "Altruist Tester Batch Report" in report
    assert "Batch ID: batch_2026-06-05T12-00-00Z" in report
    assert "- slot-01" in report
    assert "model: insight" in report


def test_batch_report_includes_aggregate_device_details(tmp_path):
    started_at = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    config = BatchConfig(
        duration_input="24h",
        duration_seconds=24 * 60 * 60,
        baud=115200,
        output_dir=tmp_path,
        devices=(
            BatchDeviceConfig(
                slot="slot-01",
                port=Path("/dev/serial/by-path/urban"),
                model="urban",
                config=Path("configs/urban.example.toml"),
                effective_config=Path("configs/urban.example.toml"),
            ),
        ),
    )
    artifacts = create_batch_artifacts(tmp_path, config=config, started_at=started_at)

    artifacts.write_report(
        "completed",
        message="Batch completed: 1/1 workers succeeded.",
        finished_at=datetime(2026, 6, 6, 12, 0, tzinfo=UTC),
        worker_results=(
            {
                "slot": "slot-01",
                "status": "completed",
                "returncode": 0,
                "stdout_log": "worker.stdout.log",
                "stderr_log": "worker.stderr.log",
            },
        ),
        aggregate={
            "verdict": "PASS_CANDIDATE",
            "devices_total": 1,
            "devices_passed": 1,
            "devices_warned": 0,
            "devices_failed": 0,
            "devices": [
                {
                    "slot": "slot-01",
                    "model": "urban",
                    "port": "/dev/serial/by-path/urban",
                    "config": "configs/urban.example.toml",
                    "device_id": "588C8140B8EC",
                    "mac": "58:8C:81:40:B8:EC",
                    "usb_serial": "58:8C:81:40:B8:EC",
                    "by_id": "/dev/serial/by-id/usb-Espressif_588C8140B8EC",
                    "by_path": "/dev/serial/by-path/urban",
                    "identity_conflicts": [],
                    "run_dir": "runs/slot-01/run",
                    "report_txt": "runs/slot-01/run/report.txt",
                    "status": "completed",
                    "verdict": "PASS_CANDIDATE",
                    "findings_count": 0,
                    "finding_messages": [],
                    "failed_checks": [],
                }
            ],
        },
    )

    report = artifacts.report_txt.read_text(encoding="utf-8")
    assert "Verdict: PASS_CANDIDATE" in report
    assert "Devices: 1 total, 1 pass, 0 warn, 0 fail" in report
    assert "- slot-01 588C8140B8EC PASS_CANDIDATE (urban, 0 findings)" in report
    assert "usb serial: 58:8C:81:40:B8:EC" in report
    assert "by-id: /dev/serial/by-id/usb-Espressif_588C8140B8EC" in report
    assert "by-path: /dev/serial/by-path/urban" in report
    assert "stdout: worker.stdout.log" in report


def test_append_sample_writes_timestamped_sensor_sample(tmp_path):
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="10m",
        duration_seconds=600,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )

    record = artifacts.append_sample(
        SensorSample(
            sensor="BME280",
            metric="temperature",
            value=24.5,
            unit="C",
        )
    )

    samples = [
        json.loads(line)
        for line in artifacts.samples_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    assert len(samples) == 1
    assert samples[0] == record.as_dict()
    assert samples[0]["ts"].endswith("Z")
    assert samples[0]["sensor"] == "BME280"
    assert samples[0]["metric"] == "temperature"
    assert samples[0]["value"] == 24.5
    assert samples[0]["unit"] == "C"
    assert samples[0]["source"] == "serial"


def test_write_report_includes_final_pass_details(tmp_path):
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="5m",
        duration_seconds=300,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )

    artifacts.write_report(
        "completed",
        finished_at=datetime(2026, 6, 5, 12, 5, tzinfo=UTC),
        details={
            "metrics_seen": True,
            "samples_seen": True,
            "serial_lines_read": 100,
            "serial_bytes_read": 2048,
            "max_serial_silence_seconds": 3.0,
            "last_dev_metrics": {
                "status": "ALIVE",
                "uptime_sec": 300,
                "boot": 1,
                "wifi_state": "OK",
                "rssi": -60,
                "tx": 3,
                "errors": {"wifi": 0, "sensor": 0, "sd": 0},
            },
            "sensor_samples_count": 14,
            "rules": {
                "verdict": "PASS_CANDIDATE",
                "status": "ok",
                "findings": [],
            },
            "sensor_presence": {
                "observed_metrics": ["humidity", "temperature"],
                "missing_metrics": [],
            },
            "sensor_ranges": {
                "status": "ok",
                "checked_samples_count": 14,
                "failure_count": 0,
                "warning_count": 0,
            },
            "sensor_flatlines": {
                "status": "ok",
                "failure_count": 0,
                "warning_count": 0,
            },
            "sensor_cadence": {
                "status": "ok",
                "failure_count": 0,
                "warning_count": 0,
            },
        },
    )

    report = artifacts.report_txt.read_text(encoding="utf-8")
    assert "Verdict:" in report
    assert "- verdict: PASS_CANDIDATE" in report
    assert "Findings:\n- none" in report
    assert "- last dev metrics: status=ALIVE, uptime=300s" in report
    assert "- observed metrics: humidity, temperature" in report


def test_write_report_includes_final_fail_details(tmp_path):
    artifacts = create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input="10m",
        duration_seconds=600,
        started_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )

    artifacts.write_report(
        "failed",
        finished_at=datetime(2026, 6, 5, 12, 10, tzinfo=UTC),
        details={
            "metrics_seen": False,
            "samples_seen": False,
            "serial_lines_read": 0,
            "serial_bytes_read": 0,
            "max_serial_silence_seconds": 600.0,
            "last_dev_metrics": None,
            "sensor_samples_count": 0,
            "rules": {
                "verdict": "FAIL",
                "status": "fail",
                "findings": [
                    {
                        "severity": "fail",
                        "code": "NO_SERIAL_OUTPUT",
                        "message": "No serial output was received",
                    }
                ],
            },
            "sensor_presence": {
                "observed_metrics": [],
                "missing_metrics": ["temperature"],
            },
            "sensor_ranges": {
                "status": "ok",
                "checked_samples_count": 0,
                "failure_count": 0,
                "warning_count": 0,
            },
        },
    )

    report = artifacts.report_txt.read_text(encoding="utf-8")
    assert "- verdict: FAIL" in report
    assert "[FAIL] NO_SERIAL_OUTPUT: No serial output was received" in report
    assert "- dev metrics seen: False" in report
    assert "- missing metrics: temperature" in report
