"""Parsers for firmware upload status lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from altruist_tester.parsers.boot_events import (
    parse_fields_with_spaces,
    parse_key_value_fields,
)

UploadChannel = Literal["connectivity", "datalog"]
UploadStatus = Literal["attempt", "success", "failure"]

_CONNECTIVITY_RE = re.compile(
    r"^\[CONNECTIVITY\]\s+(?P<status>attempt|success|failed)\s+"
    r"channel=sensors-connectivity\s+seq=(?P<sequence>\d+)"
    r"(?:\s+(?P<fields>.+?))?\s*$"
)

_DATALOG_RE = re.compile(
    r"^\[DATALOG\]\s+(?P<status>attempt|success|failed)"
    r"(?:\s+(?P<fields>.+?))?\s*$"
)


@dataclass(frozen=True, slots=True)
class UploadEvent:
    """One parsed upload status observation from firmware logs."""

    channel: UploadChannel
    status: UploadStatus
    sequence: int | None = None
    target: str | None = None
    reason: str | None = None
    raw_fields: dict[str, str] | None = None

    @property
    def is_health_check(self) -> bool:
        """Identify explicitly marked server-selection health probes."""

        fields = self.raw_fields or {}
        return self.channel == "connectivity" and fields.get("stage") == "health"

    def as_event_payload(self) -> dict[str, object]:
        """Return upload observation as an event payload."""

        return {
            "channel": self.channel,
            "status": self.status,
            "sequence": self.sequence,
            "target": self.target,
            "reason": self.reason,
            "raw_fields": self.raw_fields or {},
        }


def _format_fields(
    fields: dict[str, str],
    *,
    exclude: frozenset[str] = frozenset(),
) -> str | None:
    details = [f"{key}={value}" for key, value in fields.items() if key not in exclude]
    if not details:
        return None
    return " ".join(details)


def _parse_connectivity_event(match: re.Match[str]) -> UploadEvent | None:
    fields = parse_key_value_fields(match.group("fields") or "")
    status = match.group("status")
    target = fields.get("host")

    if status == "failed":
        reason = fields.get("reason")
        if not reason:
            return None
        details = _format_fields(fields, exclude=frozenset({"host", "reason"}))
        return UploadEvent(
            channel="connectivity",
            status="failure",
            sequence=int(match.group("sequence")),
            target=target,
            reason=f"{reason} {details}" if details else reason,
            raw_fields=fields,
        )

    return UploadEvent(
        channel="connectivity",
        status=status,
        sequence=int(match.group("sequence")),
        target=target,
        reason=_format_fields(fields, exclude=frozenset({"host"})),
        raw_fields=fields,
    )


def _parse_datalog_event(match: re.Match[str]) -> UploadEvent | None:
    fields = parse_fields_with_spaces(match.group("fields") or "")
    status = match.group("status")

    if status == "failed":
        reason = fields.get("reason")
        if not reason:
            return None
        details = _format_fields(fields, exclude=frozenset({"reason"}))
        return UploadEvent(
            channel="datalog",
            status="failure",
            reason=f"{reason} {details}" if details else reason,
            raw_fields=fields,
        )

    return UploadEvent(
        channel="datalog",
        status=status,
        reason=_format_fields(fields),
        raw_fields=fields,
    )


def parse_upload_event(line: str) -> UploadEvent | None:
    """Parse one firmware upload status line.

    Supports stable ``[CONNECTIVITY]`` and ``[DATALOG]`` firmware lines.
    Returns ``None`` for serial lines unrelated to upload delivery.
    """

    if match := _CONNECTIVITY_RE.match(line):
        return _parse_connectivity_event(match)

    if match := _DATALOG_RE.match(line):
        return _parse_datalog_event(match)
    return None
