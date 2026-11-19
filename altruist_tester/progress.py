"""Bounded worker snapshots for SSH progress, separate from run evidence."""

import json
import math
import time
from pathlib import Path


def write_worker_status(path: Path | None, state: str, **fields: object) -> None:
    """Replace a small snapshot atomically so the parent never reads half JSON."""
    if path is None:
        return
    if "message" in fields:
        fields["message"] = str(fields["message"])[:2048]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"state": state, "updated_monotonic": time.monotonic(), **fields}),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_worker_status(path: Path) -> dict[str, object] | None:
    """Read a snapshot without consulting raw UART or growing event streams."""
    try:
        with path.open(encoding="utf-8") as handle:
            payload = handle.read(16385)
    except FileNotFoundError:
        return None
    if len(payload) > 16384:
        raise ValueError(f"Worker status is too large: {path}")
    value = json.loads(payload)
    if not isinstance(value, dict) or not isinstance(value.get("state"), str):
        raise ValueError(f"Invalid worker status: {path}")
    state = value["state"]
    numeric_fields = ["updated_monotonic"]
    if state == "WAIT_PORT":
        numeric_fields.append("wait_elapsed_seconds")
        if "wait_timeout_seconds" not in value:
            raise ValueError(f"Missing wait timeout in worker status: {path}")
        if value["wait_timeout_seconds"] is not None:
            numeric_fields.append("wait_timeout_seconds")
    elif state == "CAPTURE":
        numeric_fields.extend(
            [
                "elapsed_seconds",
                "duration_seconds",
                "bytes_read",
                "current_silence_seconds",
                "dev_metrics_count",
                "sensor_samples_count",
                "structured_events_count",
            ]
        )
        if value.get("wifi_observed_elapsed_seconds") is not None:
            numeric_fields.append("wifi_observed_elapsed_seconds")
        if value.get("wifi_state") not in {None, "OK", "DISCONNECTED"}:
            raise ValueError(f"Invalid Wi-Fi state: {path}")
    elif state not in {"OPENING", "COMPLETED", "ERROR"}:
        raise ValueError(f"Unknown worker state: {state}")
    for field in numeric_fields:
        number = value.get(field)
        if (
            isinstance(number, bool)
            or not isinstance(number, int | float)
            or not math.isfinite(number)
            or number < 0
        ):
            raise ValueError(f"Invalid worker status field {field}: {path}")
    if state == "CAPTURE" and value["duration_seconds"] <= 0:
        raise ValueError(f"Invalid capture duration in worker status: {path}")
    return value
