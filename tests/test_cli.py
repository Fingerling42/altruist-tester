import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from altruist_tester import __version__
from altruist_tester.cli import _format_run_progress, app
from altruist_tester.ports import SerialPortInfo
from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
from altruist_tester.serial_logger import (
    DevMetricsSummary,
    SerialLogProgress,
    SerialLogStats,
)


def _series_with_metrics(*metrics: str) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for index, metric in enumerate(metrics):
        series.append(
            SensorSampleRecord(
                ts=f"2026-06-05T12:00:{index:02d}.000Z",
                sensor="sensor",
                metric=metric,
                value=float(index),
                unit=None,
                source="serial",
            )
        )
    return series


def _sample_record(
    sensor: str,
    metric: str,
    value: float,
    offset_seconds: int,
) -> SensorSampleRecord:
    ts = datetime(2026, 6, 5, 12, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    return SensorSampleRecord(
        ts=ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        sensor=sensor,
        metric=metric,
        value=value,
        unit=None,
        source="serial",
    )


def _series_with_records(*records: SensorSampleRecord) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for record in records:
        series.append(record)
    return series


def test_package_version_is_available():
    assert isinstance(__version__, str)
    assert __version__


def test_cli_help_runs():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Post-assembly burn-in tester" in result.output


def test_cli_version_runs():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "altruist-tester" in result.output


def test_format_run_progress_includes_elapsed_time_and_live_counters():
    progress = SerialLogProgress(
        elapsed_seconds=65.4,
        duration_seconds=600,
        lines_read=123,
        bytes_read=4567,
        dev_metrics_count=4,
        keyword_alerts_count=1,
        sensor_samples_count=28,
    )

    assert _format_run_progress(progress) == (
        "Progress  10.9% (01:05/10:00) | lines=123 bytes=4567 "
        "metrics=4 samples=28 alerts=1"
    )


def test_ports_lists_detected_serial_ports(monkeypatch):
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [
            SerialPortInfo(
                device="/dev/ttyACM0",
                description="USB JTAG/serial debug unit",
                hwid="USB VID:PID=303A:1001",
                vid=0x303A,
                pid=0x1001,
                manufacturer="Espressif",
            )
        ],
    )

    result = CliRunner().invoke(app, ["ports"])

    assert result.exit_code == 0
    assert "/dev/ttyACM0" in result.output
    assert "USB JTAG/serial debug unit" in result.output
    assert "Espressif" in result.output
    assert "VID:PID=303A:1001" in result.output


def test_ports_handles_empty_list(monkeypatch):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])

    result = CliRunner().invoke(app, ["ports"])

    assert result.exit_code == 0
    assert "No serial ports found." in result.output


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
    assert "Serial port does not exist: /dev/not-real" in result.output
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
    assert "Duration must be a positive integer" in result.output


def test_run_requires_port_or_auto():
    result = CliRunner().invoke(app, ["run", "--duration", "5s"])

    assert result.exit_code == 2
    assert "Specify --port or --auto" in result.output


def test_run_rejects_port_with_auto(tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()

    result = CliRunner().invoke(
        app,
        ["run", "--port", str(port), "--auto", "--duration", "5s"],
    )

    assert result.exit_code == 2
    assert "Use either --port or --auto" in result.output


def test_run_auto_rejects_missing_detected_ports(monkeypatch):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])

    result = CliRunner().invoke(app, ["run", "--auto", "--duration", "5s"])

    assert result.exit_code == 2
    assert "No serial ports found" in result.output


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
    assert "Multiple serial ports found" in result.output
    assert "/dev/ttyACM0" in result.output
    assert "/dev/ttyUSB0" in result.output


def test_run_accepts_valid_options(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    opened = {}

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            opened["path"] = path
            opened["baudrate"] = baudrate
            opened["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    captured = {}

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        captured["serial_port"] = serial_port
        captured["duration_seconds"] = duration_seconds
        artifacts.serial_log.write_bytes(b"hello from device\n")
        return SerialLogStats(
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
            sensor_series=_series_with_metrics("temperature", "humidity"),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert summary["port"] == str(port)
    assert summary["baud"] == 9600
    assert summary["duration"] == "10m"
    assert summary["duration_sec"] == 600
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
    assert summary["sensor_presence"]["status"] == "warn"
    assert summary["sensor_presence"]["observed_metrics"] == [
        "humidity",
        "temperature",
    ]
    assert summary["sensor_flatlines"]["status"] == "warn"
    assert summary["sensor_cadence"]["status"] == "warn"
    assert (run_dir / "serial.log").read_text() == "hello from device\n"
    assert (run_dir / "samples.jsonl").read_text() == ""
    events_text = (run_dir / "events.jsonl").read_text()
    assert "serial_opened" in events_text
    assert "serial_capture_started" in events_text
    assert "serial_capture_completed" in events_text
    assert "serial_line" not in events_text
    assert "Captured 1 serial lines" in (run_dir / "report.txt").read_text()


def test_run_auto_uses_single_detected_port(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"
    opened = {}

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            opened["path"] = path
            opened["baudrate"] = baudrate
            opened["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(lines_read=0, bytes_read=0)

    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [SerialPortInfo(device=str(port), description="only port")],
    )
    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=4,
            sensor_series=_series_with_metrics("temperature", "humidity", "P1", "P2"),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=7,
            sensor_series=_series_with_metrics(
                "temperature",
                "humidity",
                "pressure",
                "P1",
                "P2",
                "noiseAvg",
                "noiseMax",
            ),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert "Unknown expected sensor 'not-a-sensor'" in result.output
    assert not output_dir.exists()


def test_run_fails_when_expected_metric_is_missing(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=3,
            sensor_series=_series_with_metrics("temperature", "humidity", "P1"),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert "Missing expected sensor metrics: pm25" in result.output
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

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=2,
            sensor_series=_series_with_records(
                _sample_record("SCD4x", "co2", 612.0, 0),
                _sample_record("SCD4x", "co2", 612.0, 60 * 60),
            ),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert "sensor metric series failed flatline checks" in result.output
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["sensor_presence"]["status"] == "ok"
    assert summary["sensor_flatlines"]["status"] == "fail"
    assert summary["sensor_flatlines"]["failure_count"] == 1
    assert "sensor_flatlines_checked" in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_sensor_update_cadence_is_too_slow(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            sensor_samples_count=2,
            sensor_series=_series_with_records(
                _sample_record("BME280", "temperature", 24.0, 0),
                _sample_record("BME280", "temperature", 24.5, 25 * 60),
            ),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert "sensor metric series missed too many updates" in result.output
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["sensor_presence"]["status"] == "ok"
    assert summary["sensor_flatlines"]["status"] == "ok"
    assert summary["sensor_cadence"]["status"] == "fail"
    assert summary["sensor_cadence"]["failure_count"] == 1
    assert "sensor_cadence_checked" in (run_dir / "events.jsonl").read_text()


def test_run_fails_when_runtime_counters_show_reboot(monkeypatch, tmp_path):
    port = tmp_path / "ttyACM0"
    port.touch()
    output_dir = tmp_path / "runs"

    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_capture_raw_serial(
        serial_port,
        artifacts,
        duration_seconds,
        **kwargs,
    ):
        return SerialLogStats(
            lines_read=2,
            bytes_read=20,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 20},
                {"boot": 1, "uptime_sec": 5},
            ),
        )

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial", fake_capture_raw_serial
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
    assert "runtime counter checks failed" in result.output
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["runtime_counters"]["status"] == "fail"
    assert summary["runtime_counters"]["findings"][0]["code"] == "UPTIME_DECREASED"
    assert "runtime_counters_checked" in (run_dir / "events.jsonl").read_text()
