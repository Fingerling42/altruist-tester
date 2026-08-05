import json

from typer.testing import CliRunner

from altruist_tester.cli import app
from altruist_tester.ports import SerialPortInfo
from altruist_tester.serial_logger import DevMetricsSummary, SerialLogStats
from tests.cli_helpers import (
    patch_cli_capture,
    plain_output,
    release_log_stats,
    series_with_metrics,
)
from tests.helpers import sample_record, sample_series


def test_run_rejects_missing_serial_port(tmp_path):
    output_dir = tmp_path / "runs"

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            "/dev/not-real",
            "--duration",
            "5s",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 2
    assert "Serial port does not exist: /dev/not-real" in plain_output(result)
    run_dirs = list(output_dir.iterdir())
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    assert (run_dir / "serial.log").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "samples.jsonl").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "report.txt").exists()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["message"] == "Serial port does not exist: /dev/not-real"


def test_run_rejects_invalid_duration_before_opening_port():
    result = CliRunner().invoke(
        app,
        ["run", "--port", "/dev/not-real", "--duration", "nope"],
    )

    assert result.exit_code == 2
    assert "Duration must be a positive integer" in plain_output(result)


def test_run_requires_port_or_auto():
    result = CliRunner().invoke(app, ["run", "--duration", "5s"])

    assert result.exit_code == 2
    assert "Specify --port or --auto" in plain_output(result)


def test_run_rejects_port_with_auto(tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()

    result = CliRunner().invoke(
        app,
        ["run", "--port", str(port), "--auto", "--duration", "5s"],
    )

    assert result.exit_code == 2
    assert "Use either --port or --auto" in plain_output(result)


def test_run_rejects_unknown_device_model(tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--device-model",
            "unknown",
        ],
    )

    assert result.exit_code == 2
    assert "Device model must be one of" in plain_output(result)


def test_run_auto_rejects_missing_detected_ports(monkeypatch):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])

    result = CliRunner().invoke(app, ["run", "--auto", "--duration", "5s"])

    assert result.exit_code == 2
    assert "No serial ports found" in plain_output(result)


def test_run_auto_rejects_multiple_detected_ports(monkeypatch):
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [
            SerialPortInfo(device="/dev/ttyACM0", description="first"),
            SerialPortInfo(device="/dev/ttyUSB0", description="second"),
        ],
    )

    result = CliRunner().invoke(app, ["run", "--auto", "--duration", "5s"])

    assert result.exit_code == 2
    output = plain_output(result)
    assert "Multiple serial ports found" in output
    assert "/dev/ttyACM0" in output
    assert "/dev/ttyUSB0" in output


def test_run_accepts_valid_options(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    opened = {}
    captured = {}
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [
            SerialPortInfo(
                device=str(port),
                description="USB JTAG/serial debug unit",
                manufacturer="Espressif",
                serial_number="10:51:DB:01:0C:70",
            )
        ],
    )

    def capture_hook(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        captured["serial_port"] = serial_port
        captured["duration_seconds"] = duration_seconds
        # The fake capture function bypasses the raw logger, so write one raw
        # line here to verify report generation sees serial.log content.
        artifacts.serial_log.write_bytes(b"hello from device\n")

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(
            lines_read=1,
            bytes_read=18,
            dev_metrics=DevMetricsSummary(
                count=1,
                first_seen="2026-06-05T12:00:00.000Z",
                last_seen="2026-06-05T12:00:00.000Z",
                max_boot=7,
                min_uptime_sec=121,
                max_uptime_sec=121,
                max_errors={"wifi": 0, "sensor": 0, "sd": 0},
            ),
            sensor_samples_count=2,
            sensor_series=series_with_metrics("temperature", "humidity"),
            serial_device_ids=("1051DB010C70",),
        ),
        opened=opened,
        capture_hook=capture_hook,
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "10m",
            "--baud",
            "9600",
            "--device-model",
            "Urban",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert opened == {"path": str(port), "baudrate": 9600, "timeout": 1}
    assert captured["duration_seconds"] == 600
    assert f"Captured serial output for 600s on {port} at 9600 baud" in result.output
    assert "Artifacts were written under" in result.output
    run_dirs = list(output_dir.iterdir())
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["device_model"] == "urban"
    assert summary["port"] == str(port)
    assert summary["baud"] == 9600
    assert summary["duration"] == "10m"
    assert summary["duration_sec"] == 600
    assert summary["device_identity"]["device_id"] == "1051DB010C70"
    assert summary["device_identity"]["mac"] == "10:51:DB:01:0C:70"
    assert summary["device_identity"]["sources"] == {
        "serial_log": "1051DB010C70",
        "usb": "1051DB010C70",
    }
    assert summary["verdict"] == "WARN"
    assert summary["metrics_seen"] is True
    assert summary["samples_seen"] is True
    assert summary["findings"]
    assert summary["serial_lines_read"] == 1
    assert summary["serial_bytes_read"] == 18
    assert summary["dev_metrics_seen"] is True
    assert summary["dev_metrics_count"] == 1
    assert summary["first_dev_metrics_at"] == "2026-06-05T12:00:00.000Z"
    assert summary["last_dev_metrics_at"] == "2026-06-05T12:00:00.000Z"
    assert summary["max_boot"] == 7
    assert summary["min_uptime_sec"] == 121
    assert summary["max_uptime_sec"] == 121
    assert summary["max_errors"] == {"wifi": 0, "sensor": 0, "sd": 0}
    assert summary["sensor_samples_count"] == 2
    assert summary["rules"]["verdict"] == "WARN"
    assert summary["rules"]["status"] == "warn"
    assert summary["sensor_ranges"]["status"] == "ok"
    assert summary["sensor_ranges"]["checked_samples_count"] == 2
    assert summary["serial_silence"]["status"] == "ok"
    assert summary["sensor_presence"]["status"] == "warn"
    assert summary["sensor_presence"]["observed_metrics"] == [
        "humidity",
        "temperature",
    ]
    assert summary["sensor_flatlines"]["status"] == "warn"
    assert summary["sensor_cadence"]["status"] == "warn"
    assert (run_dir / "serial.log").read_text() == "hello from device\n"
    # The fake capture returns an in-memory sample series; only the real logger
    # owns writing samples.jsonl.
    assert (run_dir / "samples.jsonl").read_text() == ""
    events_text = (run_dir / "events.jsonl").read_text()
    assert "serial_opened" in events_text
    assert "serial_capture_started" in events_text
    assert "serial_capture_completed" in events_text
    assert "device_identity_detected" in events_text
    assert "device_identity_resolved" in events_text
    # Aggregated rule results belong to summary.json/report.txt; events.jsonl is
    # kept for runtime milestones and raw health observations.
    assert "sensor_ranges_checked" not in events_text
    assert "rules_evaluated" not in events_text
    assert "serial_line" not in events_text
    report_text = (run_dir / "report.txt").read_text()
    assert "Captured 1 serial lines" in report_text
    assert "Verdict:" in report_text
    assert "- verdict: WARN" in report_text
    assert "Health:" in report_text
    assert "Sensors:" in report_text
    assert "Device:" in report_text
    assert "- id: 1051DB010C70" in report_text


def test_run_auto_uses_single_detected_port(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    opened = {}

    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [SerialPortInfo(device=str(port), description="only port")],
    )
    patch_cli_capture(
        monkeypatch,
        release_log_stats(lines_read=1, bytes_read=10),
        opened=opened,
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--auto",
            "--duration",
            "5s",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert opened["path"] == str(port)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["port"] == str(port)


def test_run_passes_when_expected_metrics_are_seen(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        release_log_stats(
            sensor_samples_count=4,
            sensor_series=series_with_metrics("temperature", "humidity", "P1", "P2"),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--expect-metric",
            "temperature",
            "--expect-metric",
            "humidity",
            "--expect-metric",
            "pm10",
            "--expect-metric",
            "pm25",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["sensor_presence"]["status"] == "ok"
    assert summary["sensor_presence"]["missing_metrics"] == []


def test_run_passes_when_expected_sensors_are_seen(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        release_log_stats(
            sensor_samples_count=7,
            sensor_series=series_with_metrics(
                "temperature",
                "humidity",
                "pressure",
                "P1",
                "P2",
                "noiseAvg",
                "noiseMax",
            ),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--expect-sensor",
            "bme280",
            "--expect-sensor",
            "sds",
            "--expect-sensor",
            "ics-43434",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["sensor_presence"]["expected_sensors"] == [
        "bme280",
        "ics43434",
        "sds",
    ]
    assert summary["sensor_presence"]["missing_metrics"] == []


def test_run_uses_expected_sensors_from_config(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "urban.toml"
    config_path.write_text(
        """
[expect]
sensors = ["bme280", "sds", "ics-43434"]
""",
        encoding="utf-8",
    )

    patch_cli_capture(
        monkeypatch,
        release_log_stats(
            sensor_samples_count=7,
            sensor_series=series_with_metrics(
                "temperature",
                "humidity",
                "pressure",
                "P1",
                "P2",
                "noiseAvg",
                "noiseMax",
            ),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["config"] == str(config_path)
    assert summary["sensor_presence"]["expected_sensors"] == [
        "bme280",
        "ics43434",
        "sds",
    ]
    assert summary["sensor_presence"]["missing_metrics"] == []
    assert f"- config: {config_path}" in (run_dir / "report.txt").read_text()


def test_run_rejects_invalid_config_before_creating_artifacts(tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "profile.toml"
    config_path.write_text(
        """
[serial]
silence_fail_after = "eventually"
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 2
    assert "serial.silence_fail_after" in plain_output(result)
    assert not output_dir.exists()


def test_run_uses_serial_silence_thresholds_from_config(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "profile.toml"
    config_path.write_text(
        """
[serial]
silence_warn_after = "2s"
silence_fail_after = "4s"
""",
        encoding="utf-8",
    )

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(lines_read=0, bytes_read=0),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["serial_silence"]["status"] == "fail"
    assert summary["serial_silence"]["findings"][0]["fail_after_seconds"] == 4


def test_run_rejects_unknown_expected_sensor_before_creating_artifacts(tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--expect-sensor",
            "not-a-sensor",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 2
    assert "Unknown expected sensor 'not-a-sensor'" in plain_output(result)
    assert not output_dir.exists()


def test_run_fails_when_expected_metric_is_missing(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=3,
            sensor_series=series_with_metrics("temperature", "humidity", "P1"),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "5s",
            "--expect-metric",
            "temperature",
            "--expect-metric",
            "humidity",
            "--expect-metric",
            "pm10",
            "--expect-metric",
            "pm25",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "Missing expected sensor metrics: pm25" in plain_output(result)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["sensor_presence"]["status"] == "fail"
    assert summary["sensor_presence"]["missing_metrics"] == ["pm25"]
    assert "run_failed" in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_sensor_values_are_flatlined(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=2,
            sensor_series=sample_series(
                sample_record("SCD4x", "co2", 612.0, 0),
                sample_record("SCD4x", "co2", 612.0, 60 * 60),
            ),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "1h",
            "--expect-metric",
            "co2",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "sensor metric series failed flatline checks" in plain_output(result)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["sensor_presence"]["status"] == "ok"
    assert summary["sensor_flatlines"]["status"] == "fail"
    assert summary["sensor_flatlines"]["failure_count"] == 1
    # Final rule payloads stay in summary.json to keep events.jsonl compact.
    assert "sensor_flatlines_checked" not in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_sensor_update_cadence_is_too_slow(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=2,
            sensor_series=sample_series(
                sample_record("BME280", "temperature", 24.0, 0),
                sample_record("BME280", "temperature", 24.5, 25 * 60),
            ),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "30m",
            "--expect-metric",
            "temperature",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "sensor metric series missed too many updates" in plain_output(result)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["sensor_presence"]["status"] == "ok"
    assert summary["sensor_flatlines"]["status"] == "ok"
    assert summary["sensor_cadence"]["status"] == "fail"
    assert summary["sensor_cadence"]["failure_count"] == 1
    # Final rule payloads stay in summary.json to keep events.jsonl compact.
    assert "sensor_cadence_checked" not in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_runtime_counters_show_reboot(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(
            lines_read=2,
            bytes_read=20,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 20},
                {"boot": 1, "uptime_sec": 5},
            ),
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "30m",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "runtime counter checks failed" in plain_output(result)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["runtime_counters"]["status"] == "fail"
    assert summary["runtime_counters"]["findings"][0]["code"] == "UPTIME_DECREASED"
    # Final rule payloads stay in summary.json to keep events.jsonl compact.
    assert "runtime_counters_checked" not in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_serial_output_is_silent(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    patch_cli_capture(
        monkeypatch,
        SerialLogStats(lines_read=0, bytes_read=0),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "10m",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    assert "serial silence checks failed" in plain_output(result)
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["verdict"] == "FAIL"
    assert summary["metrics_seen"] is False
    assert summary["samples_seen"] is False
    assert summary["findings"]
    assert summary["serial_silence"]["status"] == "fail"
    assert summary["serial_silence"]["findings"][0]["code"] == "NO_SERIAL_OUTPUT"
    assert summary["rules"]["verdict"] == "FAIL"
    assert "serial_silence" in summary["rules"]["failed_checks"]
    # Final rule payloads stay in summary.json to keep events.jsonl compact.
    assert "serial_silence_checked" not in (run_dir / "events.jsonl").read_text()
