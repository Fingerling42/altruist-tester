"""Parsers for sensor values printed by development firmware."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from altruist_tester.samples import SensorSample

_DATALOG_RE = re.compile(r"\bDatalog data:\s*:?\s*(?P<payload>.+)$")
_DATALOG_ITEM_RE = re.compile(
    r"(?P<alias>[A-Za-z][A-Za-z0-9_]*):(?P<value>[+-]?\d+(?:\.\d+)?)"
)

_SKIP_JSON_KEYS = frozenset({"service_data"})

_DATALOG_ALIASES = {
    "h": ("humidity", "%"),
    "t": ("temperature", "°C"),
    "p": ("pressure", "hPa"),
    "nm": ("noiseMax", "dB"),
    "na": ("noiseAvg", "dB"),
    "p1": ("P1", "ppm"),
    "p2": ("P2", "ppm"),
    "gc": ("radiation", "µR/h"),
    "co2": ("co2", "ppm"),
    "co": ("co", "ppm"),
    "o3": ("o3", "ppm"),
    "no2": ("no2", "ppm"),
    "fa": ("fast_aqi", None),
    "ea": ("epa_aqi", None),
}

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
        try:
            payload, end = _JSON_DECODER.raw_decode(line, start)
        except json.JSONDecodeError:
            start = line.find("{", start + 1)
            continue

        samples.extend(_samples_from_json_payload(payload))
        start = line.find("{", end)

    return samples


def _parse_datalog_line(line: str) -> list[SensorSample]:
    match = _DATALOG_RE.search(line)
    if match is None:
        return []

    samples = []
    for item_match in _DATALOG_ITEM_RE.finditer(match.group("payload")):
        alias = item_match.group("alias").lower()
        metric, unit = _DATALOG_ALIASES.get(alias, (alias, None))
        value = _finite_float(item_match.group("value"))
        if value is None:
            continue

        samples.append(
            SensorSample(
                sensor="datalog",
                metric=metric,
                value=value,
                unit=unit,
                source="serial_datalog",
            )
        )

    return samples


def parse_sensor_values(line: str) -> list[SensorSample]:
    """Parse zero or more sensor samples from one serial line."""

    json_samples = _parse_json_sensor_snapshots(line)
    if json_samples:
        return json_samples
    return _parse_datalog_line(line)
