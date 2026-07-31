"""Parsers for firmware upload status lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UploadChannel = Literal["connectivity", "datalog"]
UploadStatus = Literal["attempt", "success", "failure"]

_CONNECTIVITY_ATTEMPT_RE = re.compile(
    r"^\[CONNECTIVITY\]\s+attempt\s+channel=sensors-connectivity\s+"
    r"seq=(?P<sequence>\d+)\s*$"
)
_CONNECTIVITY_SUCCESS_RE = re.compile(
    r"^\[CONNECTIVITY\]\s+success\s+channel=sensors-connectivity\s+"
    r"seq=(?P<sequence>\d+)\s+host=(?P<target>\S+)\s+code=(?P<code>-?\d+)\s*$"
)
_CONNECTIVITY_FAILURE_RE = re.compile(
    r"^\[CONNECTIVITY\]\s+failed\s+channel=sensors-connectivity\s+"
    r"seq=(?P<sequence>\d+)\s+reason=(?P<reason>\S+)"
    r"(?:\s+host=(?P<target>\S+))?"
    r"(?:\s+code=(?P<code>-?\d+))?"
    r"(?:\s+response_len=(?P<response_len>\d+))?\s*$"
)

_DATALOG_ATTEMPT_RE = re.compile(
    r"^\[DATALOG\]\s+attempt\s+payload_len=(?P<payload_len>\d+)\s+"
    r"payload_empty=(?P<payload_empty>[01])\s+"
    r"owner_self_fallback=(?P<owner_self_fallback>[01])\s*$"
)
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


def parse_upload_event(line: str) -> UploadEvent | None:
    """Parse one firmware upload status line.

    Supports stable ``[CONNECTIVITY]`` and ``[DATALOG]`` firmware lines.
    Returns ``None`` for serial lines unrelated to upload delivery.
    """

    if match := _CONNECTIVITY_ATTEMPT_RE.match(line):
        return UploadEvent(
            channel="connectivity",
            status="attempt",
            sequence=int(match.group("sequence")),
        )
    if match := _CONNECTIVITY_SUCCESS_RE.match(line):
        return UploadEvent(
            channel="connectivity",
            status="success",
            sequence=int(match.group("sequence")),
            target=match.group("target"),
            reason=f"code={match.group('code')}",
        )
    if match := _CONNECTIVITY_FAILURE_RE.match(line):
        details = [match.group("reason")]
        if match.group("code") is not None:
            details.append(f"code={match.group('code')}")
        if match.group("response_len") is not None:
            details.append(f"response_len={match.group('response_len')}")
        return UploadEvent(
            channel="connectivity",
            status="failure",
            sequence=int(match.group("sequence")),
            target=match.group("target"),
            reason=" ".join(details),
        )

    if match := _DATALOG_ATTEMPT_RE.match(line):
        details = [
            f"payload_len={match.group('payload_len')}",
            f"payload_empty={match.group('payload_empty')}",
            f"owner_self_fallback={match.group('owner_self_fallback')}",
        ]
        return UploadEvent(
            channel="datalog",
            status="attempt",
            reason=" ".join(details),
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
