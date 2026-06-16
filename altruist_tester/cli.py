"""Command line interface for the Altruist tester."""

from pathlib import Path
from typing import Annotated

import serial
import typer

from altruist_tester import __version__
from altruist_tester.artifacts import create_run_artifacts, utc_now
from altruist_tester.duration import DurationParseError, parse_duration_seconds
from altruist_tester.ports import SerialPortInfo, list_serial_ports
from altruist_tester.rules.cadence import check_sensor_cadence
from altruist_tester.rules.flatline import check_sensor_flatlines
from altruist_tester.rules.presence import (
    UnknownExpectedSensorError,
    check_sensor_presence,
    expected_metrics_for_sensors,
)
from altruist_tester.rules.runtime import check_runtime_counters
from altruist_tester.rules.silence import check_serial_silence
from altruist_tester.serial_logger import SerialLogProgress, capture_raw_serial

DEFAULT_OUTPUT_DIR = Path("runs")
RUN_CHECK_FAILURE_STATUSES = frozenset({"fail"})

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


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_run_progress(progress: SerialLogProgress) -> str:
    elapsed = min(progress.elapsed_seconds, float(progress.duration_seconds))
    return (
        f"Progress {progress.percent:5.1f}% "
        f"({_format_elapsed(elapsed)}/{_format_elapsed(progress.duration_seconds)}) "
        f"| lines={progress.lines_read} "
        f"bytes={progress.bytes_read} "
        f"quiet={_format_elapsed(progress.current_silence_seconds)} "
        f"metrics={progress.dev_metrics_count} "
        f"samples={progress.sensor_samples_count} "
        f"alerts={progress.keyword_alerts_count}"
    )


def _make_progress_printer():
    last_length = 0

    def print_progress(progress: SerialLogProgress) -> None:
        nonlocal last_length
        message = _format_run_progress(progress)
        padding = " " * max(0, last_length - len(message))
        typer.echo(f"\r{message}{padding}", err=True, nl=False)
        last_length = len(message)
        if progress.complete:
            typer.echo("", err=True)

    return print_progress


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


def _run_checks_failed(*statuses: str) -> bool:
    return any(status in RUN_CHECK_FAILURE_STATUSES for status in statuses)


def _append_check_message(message: str, check_message: str) -> str:
    return f"{message} {check_message}."


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
    expected_sensor: Annotated[
        list[str] | None,
        typer.Option(
            "--expect-sensor",
            help=(
                "Sensor preset that must produce data. "
                "Repeat the option for multiple sensors."
            ),
        ),
    ] = None,
    expected_metric: Annotated[
        list[str] | None,
        typer.Option(
            "--expect-metric",
            help=(
                "Sensor metric that must appear at least once. "
                "Repeat the option for multiple metrics."
            ),
        ),
    ] = None,
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

    expected_sensors = tuple(expected_sensor or ())
    try:
        expected_metrics_for_sensors(expected_sensors)
    except UnknownExpectedSensorError as exc:
        raise typer.BadParameter(str(exc), param_hint="--expect-sensor") from exc

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
            stats = capture_raw_serial(
                serial_port,
                artifacts,
                duration_seconds,
                progress_callback=_make_progress_printer(),
            )
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
    sensor_presence = check_sensor_presence(
        stats.sensor_series,
        expected_metrics=tuple(expected_metric or ()),
        expected_sensors=expected_sensors,
    )
    sensor_flatlines = check_sensor_flatlines(stats.sensor_series)
    sensor_cadence = check_sensor_cadence(
        stats.sensor_series,
        reference_time=finished_at,
        max_tail_window_seconds=duration_seconds,
    )
    runtime_counters = check_runtime_counters(stats.dev_metrics_records)
    serial_silence = check_serial_silence(
        lines_read=stats.lines_read,
        duration_seconds=duration_seconds,
        first_line_elapsed_seconds=stats.first_line_elapsed_seconds,
        last_line_elapsed_seconds=stats.last_line_elapsed_seconds,
        max_interline_gap_seconds=stats.max_interline_gap_seconds,
    )
    message = (
        f"Captured {stats.lines_read} serial lines "
        f"({stats.bytes_read} bytes) from {resolved_port}."
    )
    if sensor_presence.status == "fail":
        message = _append_check_message(message, sensor_presence.message)
    if sensor_flatlines.status == "fail":
        message = _append_check_message(message, sensor_flatlines.message)
    if sensor_cadence.status == "fail":
        message = _append_check_message(message, sensor_cadence.message)
    if runtime_counters.status == "fail":
        message = _append_check_message(message, runtime_counters.message)
    if serial_silence.status == "fail":
        message = _append_check_message(message, serial_silence.message)
    artifacts.append_event(
        "serial_capture_completed",
        lines_read=stats.lines_read,
        bytes_read=stats.bytes_read,
    )
    artifacts.append_event("sensor_presence_checked", **sensor_presence.as_dict())
    artifacts.append_event("sensor_flatlines_checked", **sensor_flatlines.as_dict())
    artifacts.append_event("sensor_cadence_checked", **sensor_cadence.as_dict())
    artifacts.append_event("runtime_counters_checked", **runtime_counters.as_dict())
    artifacts.append_event("serial_silence_checked", **serial_silence.as_dict())
    run_status = (
        "failed"
        if _run_checks_failed(
            sensor_presence.status,
            sensor_flatlines.status,
            sensor_cadence.status,
            runtime_counters.status,
            serial_silence.status,
        )
        else "completed"
    )
    if run_status == "failed":
        failed_checks = [
            name
            for name, status in (
                ("sensor_presence", sensor_presence.status),
                ("sensor_flatlines", sensor_flatlines.status),
                ("sensor_cadence", sensor_cadence.status),
                ("runtime_counters", runtime_counters.status),
                ("serial_silence", serial_silence.status),
            )
            if status == "fail"
        ]
        artifacts.append_event(
            "run_failed",
            reason="sensor_checks",
            failed_checks=failed_checks,
            message="; ".join(
                check_message
                for check_message, status in (
                    (sensor_presence.message, sensor_presence.status),
                    (sensor_flatlines.message, sensor_flatlines.status),
                    (sensor_cadence.message, sensor_cadence.status),
                    (runtime_counters.message, runtime_counters.status),
                    (serial_silence.message, serial_silence.status),
                )
                if status == "fail"
            ),
        )
    else:
        artifacts.append_event("run_completed")
    artifacts.write_summary(
        run_status,
        message=message,
        finished_at=finished_at,
        extra={
            "serial_lines_read": stats.lines_read,
            "serial_bytes_read": stats.bytes_read,
            "first_serial_line_elapsed_seconds": stats.first_line_elapsed_seconds,
            "last_serial_line_elapsed_seconds": stats.last_line_elapsed_seconds,
            "max_serial_interline_gap_seconds": stats.max_interline_gap_seconds,
            **stats.dev_metrics.as_dict(),
            "keyword_alerts_count": stats.keyword_alerts_count,
            "keyword_alerts": list(stats.keyword_alerts),
            "sensor_samples_count": stats.sensor_samples_count,
            "sensor_presence": sensor_presence.as_dict(),
            "sensor_flatlines": sensor_flatlines.as_dict(),
            "sensor_cadence": sensor_cadence.as_dict(),
            "runtime_counters": runtime_counters.as_dict(),
            "serial_silence": serial_silence.as_dict(),
        },
    )
    artifacts.write_report(run_status, message=message, finished_at=finished_at)
    if sensor_presence.status == "warn":
        typer.secho(
            f"Warning: {sensor_presence.message}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if sensor_flatlines.status == "warn":
        typer.secho(
            f"Warning: {sensor_flatlines.message}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if sensor_cadence.status == "warn":
        typer.secho(
            f"Warning: {sensor_cadence.message}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if runtime_counters.status == "warn":
        typer.secho(
            f"Warning: {runtime_counters.message}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if serial_silence.status == "warn":
        typer.secho(
            f"Warning: {serial_silence.message}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if sensor_presence.status == "fail":
        typer.secho(sensor_presence.message, fg=typer.colors.RED, err=True)
    if sensor_flatlines.status == "fail":
        typer.secho(sensor_flatlines.message, fg=typer.colors.RED, err=True)
    if sensor_cadence.status == "fail":
        typer.secho(sensor_cadence.message, fg=typer.colors.RED, err=True)
    if runtime_counters.status == "fail":
        typer.secho(runtime_counters.message, fg=typer.colors.RED, err=True)
    if serial_silence.status == "fail":
        typer.secho(serial_silence.message, fg=typer.colors.RED, err=True)
    if run_status == "failed":
        raise typer.Exit(code=1)
    typer.echo(
        f"Captured serial output for {duration_seconds}s on {resolved_port} at {baud} "
        f"baud. "
        f"Artifacts were written under {artifacts.run_dir}."
    )


if __name__ == "__main__":
    app()
