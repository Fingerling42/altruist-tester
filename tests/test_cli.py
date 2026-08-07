from typer.testing import CliRunner

from altruist_tester import __version__
from altruist_tester.cli import _format_run_progress, _make_progress_printer, app
from altruist_tester.serial_logger import SerialLogProgress


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
        current_silence_seconds=12.0,
        lines_read=123,
        bytes_read=4567,
        dev_metrics_count=4,
        keyword_alerts_count=1,
        sensor_samples_count=28,
    )

    assert _format_run_progress(progress) == (
        "Progress  10.9% (01:05/10:00) | lines=123 bytes=4567 quiet=00:12 "
        "metrics=4 samples=28 alerts=1"
    )


def test_progress_printer_throttles_when_stderr_is_not_tty(capsys):
    printer = _make_progress_printer()

    printer(
        SerialLogProgress(
            elapsed_seconds=0,
            duration_seconds=900,
            current_silence_seconds=0,
            lines_read=1,
            bytes_read=12,
            dev_metrics_count=0,
            keyword_alerts_count=0,
            sensor_samples_count=0,
        )
    )
    printer(
        SerialLogProgress(
            elapsed_seconds=60,
            duration_seconds=900,
            current_silence_seconds=1,
            lines_read=2,
            bytes_read=24,
            dev_metrics_count=0,
            keyword_alerts_count=0,
            sensor_samples_count=0,
        )
    )
    printer(
        SerialLogProgress(
            elapsed_seconds=301,
            duration_seconds=900,
            current_silence_seconds=2,
            lines_read=3,
            bytes_read=36,
            dev_metrics_count=0,
            keyword_alerts_count=0,
            sensor_samples_count=0,
        )
    )
    printer(
        SerialLogProgress(
            elapsed_seconds=900,
            duration_seconds=900,
            current_silence_seconds=0,
            lines_read=4,
            bytes_read=48,
            dev_metrics_count=0,
            keyword_alerts_count=0,
            sensor_samples_count=0,
            complete=True,
        )
    )

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 3
    assert "(00:00/15:00)" in lines[0]
    assert "(05:01/15:00)" in lines[1]
    assert "(15:00/15:00)" in lines[2]
