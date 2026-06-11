"""Run artifact helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from altruist_tester.samples import SensorSample, SensorSampleRecord

ARTIFACT_FILENAMES = ("serial.log", "events.jsonl", "samples.jsonl")
_RUN_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> datetime:
    """Return the current UTC time."""

    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    """Format a timestamp for JSON artifacts."""

    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_run_id_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H-%M-%SZ")


def device_hint_from_port(port: Path) -> str:
    """Build a stable-enough device hint from a serial port path."""

    hint = port.name or "unknown"
    hint = _RUN_ID_SAFE_RE.sub("-", hint).strip("-_.")
    return hint or "unknown"


@dataclass(slots=True)
class RunArtifacts:
    """Paths and metadata for one test run."""

    run_id: str
    run_dir: Path
    started_at: datetime
    port: Path
    baud: int
    duration_input: str
    duration_seconds: int
    serial_log: Path = field(init=False)
    events_jsonl: Path = field(init=False)
    samples_jsonl: Path = field(init=False)
    summary_json: Path = field(init=False)
    report_txt: Path = field(init=False)

    def __post_init__(self) -> None:
        self.serial_log = self.run_dir / "serial.log"
        self.events_jsonl = self.run_dir / "events.jsonl"
        self.samples_jsonl = self.run_dir / "samples.jsonl"
        self.summary_json = self.run_dir / "summary.json"
        self.report_txt = self.run_dir / "report.txt"

    def append_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """Append one structured event to events.jsonl."""

        event = {
            "ts": format_timestamp(utc_now()),
            "type": event_type,
            **payload,
        }
        with self.events_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def append_sample(self, sample: SensorSample) -> SensorSampleRecord:
        """Append one timestamped sensor sample to samples.jsonl."""

        record = {
            "ts": format_timestamp(utc_now()),
            **sample.as_payload(),
        }
        with self.samples_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return SensorSampleRecord.from_mapping(record)

    def write_summary(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write the current machine-readable run summary."""

        summary: dict[str, Any] = {
            "status": status,
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "started_at": format_timestamp(self.started_at),
            "port": str(self.port),
            "baud": self.baud,
            "duration": self.duration_input,
            "duration_sec": self.duration_seconds,
            "artifacts": {
                "serial_log": str(self.serial_log),
                "events_jsonl": str(self.events_jsonl),
                "samples_jsonl": str(self.samples_jsonl),
                "summary_json": str(self.summary_json),
                "report_txt": str(self.report_txt),
            },
        }
        if finished_at is not None:
            summary["finished_at"] = format_timestamp(finished_at)
        if message is not None:
            summary["message"] = message
        if extra is not None:
            summary.update(extra)

        self.summary_json.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_report(
        self,
        status: str,
        *,
        message: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        """Write a human-readable run report."""

        lines = [
            "Altruist Tester Run Report",
            "===========================",
            "",
            f"Run ID: {self.run_id}",
            f"Status: {status}",
            f"Started at: {format_timestamp(self.started_at)}",
        ]
        if finished_at is not None:
            lines.append(f"Finished at: {format_timestamp(finished_at)}")
        lines.extend(
            [
                f"Port: {self.port}",
                f"Baud: {self.baud}",
                f"Duration: {self.duration_input} ({self.duration_seconds}s)",
            ]
        )
        if message is not None:
            lines.extend(["", message])
        lines.extend(
            [
                "",
                "Artifacts:",
                f"- serial log: {self.serial_log}",
                f"- events: {self.events_jsonl}",
                f"- samples: {self.samples_jsonl}",
                f"- summary: {self.summary_json}",
            ]
        )

        self.report_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_run_artifacts(
    output_dir: Path,
    *,
    port: Path,
    baud: int,
    duration_input: str,
    duration_seconds: int,
    started_at: datetime | None = None,
) -> RunArtifacts:
    """Create the directory and initial files for one run."""

    started_at = started_at or utc_now()
    run_id = f"{_format_run_id_time(started_at)}_{device_hint_from_port(port)}"
    run_dir = output_dir / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = output_dir / f"{run_id}-{suffix}"

    run_dir.mkdir(parents=True)
    artifacts = RunArtifacts(
        run_id=run_dir.name,
        run_dir=run_dir,
        started_at=started_at,
        port=port,
        baud=baud,
        duration_input=duration_input,
        duration_seconds=duration_seconds,
    )

    for filename in ARTIFACT_FILENAMES:
        (run_dir / filename).touch()
    artifacts.write_summary("running")
    artifacts.write_report("running", message="Run artifacts were initialized.")
    artifacts.append_event(
        "run_started",
        port=str(port),
        baud=baud,
        duration=duration_input,
        duration_sec=duration_seconds,
    )
    return artifacts
