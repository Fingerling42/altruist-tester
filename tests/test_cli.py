from typer.testing import CliRunner

from altruist_tester import __version__
from altruist_tester.cli import _format_run_progress, app
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
