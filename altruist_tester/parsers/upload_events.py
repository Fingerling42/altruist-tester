"""Parsers for firmware upload status lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from altruist_tester.parsers.boot_events import parse_key_value_fields

UploadChannel = Literal["connectivity", "datalog"]
UploadStatus = Literal["attempt", "success", "failure"]

_CONNECTIVITY_RE = re.compile(
    r"^\[CONNECTIVITY\]\s+(?P<status>attempt|success|failed)\s+"
    r"channel=sensors-connectivity\s+seq=(?P<sequence>\d+)"
    r"(?:\s+(?P<fields>.+?))?\s*$"
)

_DATALOG_ATTEMPT_RE = re.compile(r"^\[DATALOG\]\s+attempt(?:\s+(?P<fields>.+?))?\s*$")
_DATALOG_SUCCESS_RE = re.compile(
    r"^\[DATALOG\]\s+success\s+response_len=(?P<response_len>\d+)\s*$"
)
_DATALOG_FAILURE_RE = re.compile(
    r"^\[DATALOG\]\s+failed\s+reason=(?P<reason>\S+)"
    r"(?:\s+code=(?P<code>-?\d+))?"
    r"(?:\s+message=(?P<message>.*?))?"
    r"(?:\s+response_len=(?P<response_len>\d+))?\s*$"
)


@dataclass(frozen=True, slots=True)
class UploadEvent:
    """One parsed upload status observation from firmware logs."""

    channel: UploadChannel
    status: UploadStatus
    sequence: int | None = None
    target: str | None = None
    reason: str | None = None

    def as_event_payload(self) -> dict[str, object]:
        """Return upload observation as an event payload."""

        return {
            "channel": self.channel,
            "status": self.status,
            "sequence": self.sequence,
            "target": self.target,
            "reason": self.reason,
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
        )

    return UploadEvent(
        channel="connectivity",
        status=status,
        sequence=int(match.group("sequence")),
        target=target,
        reason=_format_fields(fields, exclude=frozenset({"host"})),
    )


def parse_upload_event(line: str) -> UploadEvent | None:
    """Parse one firmware upload status line.

    Supports stable ``[CONNECTIVITY]`` and ``[DATALOG]`` firmware lines.
    Returns ``None`` for serial lines unrelated to upload delivery.
    """

    if match := _CONNECTIVITY_RE.match(line):
        return _parse_connectivity_event(match)

    if match := _DATALOG_ATTEMPT_RE.match(line):
        fields = parse_key_value_fields(match.group("fields") or "")
        if not fields:
            return None
        return UploadEvent(
            channel="datalog",
            status="attempt",
            reason=_format_fields(fields),
        )
    if match := _DATALOG_SUCCESS_RE.match(line):
        return UploadEvent(
            channel="datalog",
            status="success",
            reason=f"response_len={match.group('response_len')}",
        )
    if match := _DATALOG_FAILURE_RE.match(line):
        details = [match.group("reason")]
        if match.group("code") is not None:
            details.append(f"code={match.group('code')}")
        if match.group("message") is not None:
            details.append(f"message={match.group('message')}")
        if match.group("response_len") is not None:
            details.append(f"response_len={match.group('response_len')}")
        return UploadEvent(
            channel="datalog",
            status="failure",
            reason=" ".join(details),
        )
    return None
