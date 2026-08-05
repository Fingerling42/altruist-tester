"""Parsers for sensor values printed by firmware logs."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from altruist_tester.samples import SensorSample

_DATALOG_ITEM_RE = re.compile(
    r"(?P<alias>[A-Za-z][A-Za-z0-9_]*):(?P<value>[+-]?\d+(?:\.\d+)?)"
)
_PAYLOAD_RE = re.compile(r"\[PAYLOAD\]\s+(?P<metadata>.+)$")
_PAYLOAD_FIELD_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")

_SKIP_JSON_KEYS = frozenset({"service_data"})
_SENSOR_SAMPLE_PAYLOAD_CHANNELS = frozenset({"datalog"})

_DATALOG_ALIASES = {
    "h": ("humidity", "%"),
    "t": ("temperature", "°C"),
    "p": ("pressure", "Pa"),
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


def parse_payload_metadata(line: str) -> dict[str, str] | None:
    """Parse metadata from one stable firmware ``[PAYLOAD]`` line.

    The firmware can prepend timestamps or normal log-level prefixes before
    the stable tag, so matching intentionally searches inside the line.

    :param line: Decoded UART line.
    :returns: Parsed key/value fields, or ``None`` for non-payload lines.
    """

    match = _PAYLOAD_RE.search(line)
    if match is None:
        return None

    metadata = match.group("metadata")
    field_matches = list(_PAYLOAD_FIELD_RE.finditer(metadata))
    if not field_matches:
        return {}

    fields: dict[str, str] = {}
    for index, field_match in enumerate(field_matches):
        value_start = field_match.end()
        value_end = (
            field_matches[index + 1].start()
            if index + 1 < len(field_matches)
            else len(metadata)
        )
        fields[field_match.group("key")] = metadata[value_start:value_end].strip()
    return fields


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


def _samples_from_compact_payload(payload: str, *, source: str) -> list[SensorSample]:
    samples = []
    for item_match in _DATALOG_ITEM_RE.finditer(payload):
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
                source=source,
            )
        )

    return samples


def _parse_payload_line(line: str) -> list[SensorSample]:
    fields = parse_payload_metadata(line)
    if fields is None:
        return []

    channel = fields.get("channel")
    if channel not in _SENSOR_SAMPLE_PAYLOAD_CHANNELS:
        return []

    sample = fields.get("sample")
    if not sample or fields.get("sample_available") == "0":
        return []

    return _samples_from_compact_payload(sample, source="serial_payload_datalog")


def parse_sensor_values(line: str) -> list[SensorSample]:
    """Parse zero or more sensor samples from one serial line.

    Supports JSON sensor snapshots and the stable firmware ``[PAYLOAD]``
    contract. Unknown compact aliases inside ``sample=`` are preserved as
    metric names so new firmware values can still be inspected in artifacts.
    """

    json_samples = _parse_json_sensor_snapshots(line)
    if json_samples:
        return json_samples
    payload_samples = _parse_payload_line(line)
    if payload_samples:
        return payload_samples
    return []
