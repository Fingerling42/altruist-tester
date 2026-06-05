"""Command line interface for the Altruist tester."""

from pathlib import Path
from typing import Annotated

import serial
import typer

from altruist_tester import __version__
from altruist_tester.artifacts import create_run_artifacts, utc_now
from altruist_tester.duration import DurationParseError, parse_duration_seconds
from altruist_tester.ports import SerialPortInfo, list_serial_ports
from altruist_tester.serial_logger import capture_raw_serial

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


def _format_port_info(port: SerialPortInfo) -> str:
    parts = [port.device]
    details = []
    if port.description:
        details.append(port.description)
    if port.manufacturer:
        details.append(port.manufacturer)
    if port.vid_pid:
        details.append(f"VID:PID={port.vid_pid}")
    if port.hwid:
        details.append(f"HWID={port.hwid}")
    if details:
        parts.append(" - " + "; ".join(details))
    return "".join(parts)


def _print_ports(port_infos: list[SerialPortInfo]) -> None:
    if not port_infos:
        typer.echo("No serial ports found.")
        return

    for port_info in port_infos:
        typer.echo(_format_port_info(port_info))


def _resolve_run_port(port: Path | None, auto: bool) -> Path:
    if port is not None and auto:
        raise typer.BadParameter("Use either --port or --auto, not both.")
    if port is not None:
        return port
    if not auto:
        raise typer.BadParameter("Specify --port or --auto.")

    port_infos = list_serial_ports()
    if len(port_infos) == 1:
        return Path(port_infos[0].device)

    if not port_infos:
        typer.secho("No serial ports found. Connect a device or use --port.", err=True)
    else:
        typer.secho(
            "Multiple serial ports found. Choose one with --port:",
            err=True,
        )
        for port_info in port_infos:
            typer.echo(f"  {_format_port_info(port_info)}", err=True)
    raise typer.Exit(code=2)


@app.command()
def ports() -> None:
    """List detected serial ports."""

    _print_ports(list_serial_ports())


@app.command()
def run(
    port: Annotated[
        Path | None,
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
    ] = None,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Use the only detected serial port.",
        ),
    ] = False,
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

    try:
        resolved_port = _resolve_run_port(port, auto)
    except typer.BadParameter as exc:
        raise typer.BadParameter(str(exc), param_hint="--port") from exc

    artifacts = create_run_artifacts(
        output_dir,
        port=resolved_port,
        baud=baud,
        duration_input=duration,
        duration_seconds=duration_seconds,
    )

    if not resolved_port.exists():
        message = f"Serial port does not exist: {resolved_port}"
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
        with serial.Serial(str(resolved_port), baudrate=baud, timeout=1) as serial_port:
            artifacts.append_event("serial_opened", port=str(resolved_port), baud=baud)
            artifacts.append_event("serial_capture_started")
            stats = capture_raw_serial(serial_port, artifacts, duration_seconds)
    except serial.SerialException as exc:
        message = f"Could not open serial port {resolved_port}: {exc}"
        artifacts.append_event(
            "run_failed", reason="serial_open_failed", message=message
        )
        finished_at = utc_now()
        artifacts.write_summary("failed", message=message, finished_at=finished_at)
        artifacts.write_report("failed", message=message, finished_at=finished_at)
        typer.secho(message, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    finished_at = utc_now()
    message = (
        f"Captured {stats.lines_read} serial lines "
        f"({stats.bytes_read} bytes) from {resolved_port}."
    )
    artifacts.append_event(
        "serial_capture_completed",
        lines_read=stats.lines_read,
        bytes_read=stats.bytes_read,
    )
    artifacts.append_event("run_completed")
    artifacts.write_summary(
        "completed",
        message=message,
        finished_at=finished_at,
        extra={
            "serial_lines_read": stats.lines_read,
            "serial_bytes_read": stats.bytes_read,
            **stats.dev_metrics.as_dict(),
        },
    )
    artifacts.write_report("completed", message=message, finished_at=finished_at)
    typer.echo(
        f"Captured serial output for {duration_seconds}s on {resolved_port} at {baud} "
        f"baud. "
        f"Artifacts were written under {artifacts.run_dir}."
    )


if __name__ == "__main__":
    app()
