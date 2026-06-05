"""Command line interface for the Altruist tester."""

from pathlib import Path
from typing import Annotated

import serial
import typer

from altruist_tester import __version__
from altruist_tester.artifacts import create_run_artifacts, utc_now
from altruist_tester.duration import DurationParseError, parse_duration_seconds

DEFAULT_OUTPUT_DIR = Path("runs")

app = typer.Typer(
    help=(
        "Post-assembly burn-in tester for Altruist devices. "
        "The initial workflow focuses on one USB-C serial device."
    ),
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"altruist-tester {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            help="Show the installed tester version.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Run Altruist tester commands."""


@app.command()
def run(
    port: Annotated[
        Path,
        typer.Option(
            "--port",
            help="Serial port path, for example /dev/ttyACM0.",
            exists=False,
            file_okay=True,
            dir_okay=False,
            writable=False,
            readable=False,
            resolve_path=False,
        ),
    ] = ...,
    duration: Annotated[
        str,
        typer.Option(
            "--duration",
            help="Test duration, for example 30s, 10m, 2h, or 24h.",
        ),
    ] = ...,
    baud: Annotated[
        int,
        typer.Option(
            "--baud",
            min=1,
            help="Serial baud rate.",
        ),
    ] = 115200,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory where run artifacts will be written.",
            file_okay=False,
            dir_okay=True,
            resolve_path=False,
        ),
    ] = DEFAULT_OUTPUT_DIR,
) -> None:
    """Run a USB-C serial burn-in test for one device."""

    try:
        duration_seconds = parse_duration_seconds(duration)
    except DurationParseError as exc:
        raise typer.BadParameter(str(exc), param_hint="--duration") from exc

    artifacts = create_run_artifacts(
        output_dir,
        port=port,
        baud=baud,
        duration_input=duration,
        duration_seconds=duration_seconds,
    )

    if not port.exists():
        message = f"Serial port does not exist: {port}"
        artifacts.append_event(
            "run_failed", reason="missing_serial_port", message=message
        )
        finished_at = utc_now()
        artifacts.write_summary("failed", message=message, finished_at=finished_at)
        artifacts.write_report("failed", message=message, finished_at=finished_at)
        typer.secho(
            message,
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        with serial.Serial(str(port), baudrate=baud, timeout=1):
            pass
    except serial.SerialException as exc:
        message = f"Could not open serial port {port}: {exc}"
        artifacts.append_event(
            "run_failed", reason="serial_open_failed", message=message
        )
        finished_at = utc_now()
        artifacts.write_summary("failed", message=message, finished_at=finished_at)
        artifacts.write_report("failed", message=message, finished_at=finished_at)
        typer.secho(message, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    artifacts.append_event("serial_opened", port=str(port), baud=baud)
    finished_at = utc_now()
    message = "Serial port opened successfully; raw serial logging will be added next."
    artifacts.write_summary("completed", message=message, finished_at=finished_at)
    artifacts.write_report("completed", message=message, finished_at=finished_at)
    typer.echo(
        f"Ready to run for {duration_seconds}s on {port} at {baud} baud. "
        f"Artifacts were written under {artifacts.run_dir}."
    )


if __name__ == "__main__":
    app()
