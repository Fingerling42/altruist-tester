"""Parsers for sensor values printed by firmware logs."""

from __future__ import annotations

import json
import math
from typing import Any

from altruist_tester.samples import SensorSample
from altruist_tester.sensor_observations import parse_sensor_observation

_SKIP_JSON_KEYS = frozenset({"service_data"})

_JSON_DECODER = json.JSONDecoder()


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _samples_from_json_payload(payload: Any) -> list[SensorSample]:
    if not isinstance(payload, dict):
        return []

    samples = []
    for sensor, sensor_payload in payload.items():
        if sensor in _SKIP_JSON_KEYS or not isinstance(sensor_payload, dict):
            continue

        for metric, metric_payload in sensor_payload.items():
            if not isinstance(metric_payload, dict) or "value" not in metric_payload:
                continue

            value = _finite_float(metric_payload.get("value"))
            if value is None:
                continue

            unit_value = metric_payload.get("units")
            samples.append(
                SensorSample(
                    sensor=str(sensor),
                    metric=str(metric),
                    value=value,
                    unit=None if unit_value is None else str(unit_value),
                )
            )

    return samples


def _parse_json_sensor_snapshots(line: str) -> list[SensorSample]:
    samples: list[SensorSample] = []
    start = line.find("{")
    while start != -1:
        # Firmware can print JSON snapshots after a log prefix, and more than
        # one JSON object may appear in a single UART line.
        try:
            payload, end = _JSON_DECODER.raw_decode(line, start)
        except json.JSONDecodeError:
            start = line.find("{", start + 1)
            continue

        samples.extend(_samples_from_json_payload(payload))
        start = line.find("{", end)

    return samples


def parse_sensor_values(line: str) -> list[SensorSample]:
    """Extract full JSON sensor values for diagnostics, without acceptance policy."""

    if '"_observations"' in line:
        try:
            snapshot = parse_sensor_observation(line)
        except ValueError:
            return []
        assert snapshot is not None
        return [
            SensorSample(
                sensor=snapshot["sensor"],
                metric=item["metric"],
                value=item["value"],
                unit=item["unit"],
                source="serial_observation_v1",
                observation={k: item[k] for k in ("seq", "measured_ms", "state")},
            )
            for item in snapshot["metrics"]
            if snapshot["state"] == "ok" and item["state"] == "ok"
        ]
    return _parse_json_sensor_snapshots(line)
