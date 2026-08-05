import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
from altruist_tester.serial_logger import SerialLogStats

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def series_with_metrics(*metrics: str) -> SensorSampleSeries:
    series = SensorSampleSeries()
    # Keep generated values inside the default ranges so these tests isolate
    # CLI/rules wiring instead of exercising range validation.
    sample_values = {
        "co2": 600.0,
        "humidity": 45.0,
        "noiseAvg": 45.0,
        "noiseMax": 55.0,
        "P1": 10.0,
        "P2": 5.0,
        "pm10": 10.0,
        "pm25": 5.0,
        "pressure": 1013.25,
        "temperature": 24.0,
    }
    for index, metric in enumerate(metrics):
        series.append(
            SensorSampleRecord(
                ts=f"2026-06-05T12:00:{index:02d}.000Z",
                sensor="sensor",
                metric=metric,
                value=sample_values.get(metric, float(index)),
                unit=None,
                source="serial",
            )
        )
    return series


def sample_record(
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


def series_with_records(*records: SensorSampleRecord) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for record in records:
        series.append(record)
    return series


def release_log_stats(
    *,
    lines_read: int = 2,
    bytes_read: int = 20,
    sensor_series: SensorSampleSeries | None = None,
    sensor_samples_count: int | None = None,
) -> SerialLogStats:
    series = sensor_series or series_with_metrics("temperature")
    return SerialLogStats(
        lines_read=lines_read,
        bytes_read=bytes_read,
        dev_metrics_records=(
            {"boot": 1, "uptime_sec": 10, "reset_reason": "power_on_reset"},
        ),
        sensor_samples_count=sensor_samples_count or series.count(),
        sensor_series=series,
    )


def patch_cli_capture(
    monkeypatch,
    stats: SerialLogStats,
    *,
    opened: dict[str, object] | None = None,
    capture_hook=None,
) -> None:
    # CLI tests should cover command behavior without opening real serial
    # devices or waiting for the raw logger loop.
    class FakeSerial:
        def __init__(self, path, baudrate, timeout):
            if opened is not None:
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
        if capture_hook is not None:
            capture_hook(serial_port, artifacts, duration_seconds, **kwargs)
        return stats

    monkeypatch.setattr("altruist_tester.cli.serial.Serial", FakeSerial)
    monkeypatch.setattr(
        "altruist_tester.cli.capture_raw_serial",
        fake_capture_raw_serial,
    )


def plain_output(result) -> str:
    """Return CLI output without terminal styling escape sequences."""

    return _ANSI_ESCAPE_RE.sub("", result.output)


def worker_arg(args, option: str) -> str:
    return str(args[args.index(option) + 1])


def fake_worker_run_dir(args) -> Path:
    output = Path(worker_arg(args, "--output-dir"))
    return output / f"run-{output.name}"


def write_fake_worker_summary(
    args,
    *,
    summary: dict[str, object] | None = None,
    verdict: str = "PASS_CANDIDATE",
    status: str = "completed",
) -> Path:
    run_dir = fake_worker_run_dir(args)
    run_dir.mkdir()
    if summary is None:
        summary = {
            "status": status,
            "run_dir": str(run_dir),
            "verdict": verdict,
            "device_identity": {},
            "findings": [{"severity": "warn"}] if verdict == "WARN" else [],
            "rules": {"failed_checks": []},
        }
    else:
        summary = {**summary, "run_dir": str(run_dir)}
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


class FakeBatchWorkerProcess:
    def __init__(
        self,
        args,
        stdout,
        stderr,
        text,
        *,
        returncode: int = 0,
        summary: dict[str, object] | None = None,
        report_text: str | None = None,
        write_summary: bool = True,
    ):
        self.args = list(args)
        self.returncode = returncode
        if write_summary:
            run_dir = write_fake_worker_summary(self.args, summary=summary)
            if report_text is not None:
                (run_dir / "report.txt").write_text(report_text, encoding="utf-8")
        stdout.write("worker stdout\n")
        stderr.write("worker stderr\n")

    def poll(self):
        return self.returncode


def patch_batch_worker_processes(
    monkeypatch,
    *,
    started: list[FakeBatchWorkerProcess] | None = None,
    returncodes_by_port: dict[str, int] | None = None,
    summaries_by_port: dict[str, dict[str, object]] | None = None,
    reports_by_port: dict[str, str] | None = None,
    start_failures_by_port: dict[str, str] | None = None,
) -> None:
    returncodes_by_port = returncodes_by_port or {}
    summaries_by_port = summaries_by_port or {}
    reports_by_port = reports_by_port or {}
    start_failures_by_port = start_failures_by_port or {}

    class FakeProcess(FakeBatchWorkerProcess):
        def __init__(self, args, stdout, stderr, text):
            port = worker_arg(args, "--port")
            if port in start_failures_by_port:
                raise OSError(start_failures_by_port[port])
            returncode = returncodes_by_port.get(port, 0)
            super().__init__(
                args,
                stdout,
                stderr,
                text,
                returncode=returncode,
                summary=summaries_by_port.get(port),
                report_text=reports_by_port.get(port),
                write_summary=returncode == 0,
            )
            if started is not None:
                started.append(self)

    monkeypatch.setattr("altruist_tester.cli.subprocess.Popen", FakeProcess)
