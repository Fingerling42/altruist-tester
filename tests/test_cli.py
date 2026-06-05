import json

from typer.testing import CliRunner

from altruist_tester import __version__
from altruist_tester.cli import app


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

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)

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
    assert f"Ready to run for 600s on {port} at 9600 baud" in result.output
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
    assert (run_dir / "serial.log").read_text() == ""
    assert (run_dir / "samples.jsonl").read_text() == ""
    assert "serial_opened" in (run_dir / "events.jsonl").read_text()
    assert "Serial port opened successfully" in (run_dir / "report.txt").read_text()
