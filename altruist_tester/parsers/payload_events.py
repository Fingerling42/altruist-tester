"""Parser for stable firmware payload metadata events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from altruist_tester.parsers.boot_events import (
    parse_fields_with_spaces,
    parse_optional_bool,
    parse_optional_int,
)

_PAYLOAD_RE = re.compile(r"\[PAYLOAD\](?:\s+(?P<metadata>.*))?$")
_PAYLOAD_CHANNELS = frozenset({"datalog", "sensors-connectivity", "custom-http"})


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

    return parse_fields_with_spaces(match.group("metadata") or "")


@dataclass(frozen=True, slots=True)
class PayloadObservation:
    """Parsed payload metadata from one stable ``[PAYLOAD]`` line."""

    channel: str | None = None
    encoding: str | None = None
    encrypted: bool | None = None
    payload_len: int | None = None
    raw_fields: dict[str, str] | None = None

    @property
    def malformed_reason(self) -> str | None:
        """Classify channel errors without treating future names as known channels."""

        if not self.channel:
            return "missing_channel"
        if self.channel not in _PAYLOAD_CHANNELS:
            return "unknown_channel"
        return None

    def as_event_payload(self) -> dict[str, Any]:
        """Return payload metadata as a JSON-friendly event payload."""

        raw_fields = dict(self.raw_fields or {})
        raw_fields.pop("sample", None)
        return {
            "channel": self.channel,
            "encoding": self.encoding,
            "encrypted": self.encrypted,
            "payload_len": self.payload_len,
            "raw_fields": raw_fields,
        }


def payload_observation_from_fields(fields: dict[str, str]) -> PayloadObservation:
    """Build a :class:`PayloadObservation` from parsed key/value fields."""

    return PayloadObservation(
        channel=fields.get("channel"),
        encoding=fields.get("encoding"),
        encrypted=parse_optional_bool(fields.get("encrypted")),
        payload_len=parse_optional_int(fields.get("payload_len")),
        raw_fields=fields,
    )


def parse_payload_observation(line: str) -> PayloadObservation | None:
    """Parse one current firmware ``[PAYLOAD]`` metadata line."""

    fields = parse_payload_metadata(line)
    if fields is None:
        return None
    return payload_observation_from_fields(fields)
