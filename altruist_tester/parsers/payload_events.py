"""Parser for stable firmware payload metadata events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PAYLOAD_RE = re.compile(r"\[PAYLOAD\]\s+(?P<metadata>.+)$")
_PAYLOAD_FIELD_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")


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


def _parse_bool(fields: dict[str, str], key: str) -> bool | None:
    value = fields.get(key)
    if value is None:
        return None
    if value in {"0", "false", "False"}:
        return False
    if value in {"1", "true", "True"}:
        return True
    return None


def _parse_int(fields: dict[str, str], key: str) -> int | None:
    value = fields.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class PayloadObservation:
    """Parsed payload metadata from one stable ``[PAYLOAD]`` line."""

    channel: str | None = None
    encoding: str | None = None
    encrypted: bool | None = None
    payload_len: int | None = None
    sample_available: bool | None = None
    raw_fields: dict[str, str] | None = None

    def as_event_payload(self) -> dict[str, Any]:
        """Return payload metadata as a JSON-friendly event payload."""

        raw_fields = dict(self.raw_fields or {})
        raw_fields.pop("sample", None)
        return {
            "channel": self.channel,
            "encoding": self.encoding,
            "encrypted": self.encrypted,
            "payload_len": self.payload_len,
            "sample_available": self.sample_available,
            "raw_fields": raw_fields,
        }


def payload_observation_from_fields(fields: dict[str, str]) -> PayloadObservation:
    """Build a :class:`PayloadObservation` from parsed key/value fields."""

    return PayloadObservation(
        channel=fields.get("channel"),
        encoding=fields.get("encoding"),
        encrypted=_parse_bool(fields, "encrypted"),
        payload_len=_parse_int(fields, "payload_len"),
        sample_available=_parse_bool(fields, "sample_available"),
        raw_fields=fields,
    )


def parse_payload_observation(line: str) -> PayloadObservation | None:
    """Parse one current firmware ``[PAYLOAD]`` metadata line."""

    fields = parse_payload_metadata(line)
    if fields is None or not fields:
        return None
    return payload_observation_from_fields(fields)
