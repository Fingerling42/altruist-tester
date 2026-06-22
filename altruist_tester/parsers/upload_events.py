"""Parsers for firmware upload status lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UploadChannel = Literal["connectivity", "datalog"]
UploadStatus = Literal["attempt", "target", "success", "failure", "skipped", "warning"]

_MAP_ATTEMPT_RE = re.compile(r"\[Map#(?P<sequence>\d+)\]\s+Send attempt")
_MAP_POST_RE = re.compile(r"\[Map#(?P<sequence>\d+)\]\s+POST to (?P<target>\S+)")
_MAP_SUCCESS_RE = re.compile(
    r"\[Map#(?P<sequence>\d+)\]\s+OK, POST succeeded -> (?P<target>\S+)"
)
_MAP_FAILURE_RE = re.compile(r"\[Map\]\s+FAILED:\s*(?P<reason>.+)$")
_MAP_SKIP_RE = re.compile(r"\[Map\]\s+Skipping send:\s*(?P<reason>.+)$")
_MAP_POST_SKIP_RE = re.compile(
    r"\[Map#(?P<sequence>\d+)\]\s+skipped:\s*(?P<reason>.+)$"
)
_MAP_WARNING_RE = re.compile(r"\[Map\]\s+WARNING:\s*(?P<reason>.+)$")

_DATALOG_SENDING_RE = re.compile(r"\[Datalog\]\s+Sending:")
_DATALOG_SUCCESS_RE = re.compile(r"\[Datalog\]\s+OK,\s*result:\s*(?P<result>.*)$")
_DATALOG_FAILURE_RE = re.compile(r"\[Datalog\]\s+FAILED\b")
_DATALOG_WARNING_RE = re.compile(r"\[Datalog\]\s+WARNING:\s*(?P<reason>.+)$")


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

    Supports Robonomics Map/connectivity lines and Robonomics Datalog lines.
    Returns ``None`` for serial lines unrelated to upload delivery.
    """

    if match := _MAP_ATTEMPT_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="attempt",
            sequence=int(match.group("sequence")),
        )
    if match := _MAP_POST_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="target",
            sequence=int(match.group("sequence")),
            target=match.group("target"),
        )
    if match := _MAP_SUCCESS_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="success",
            sequence=int(match.group("sequence")),
            target=match.group("target"),
        )
    if match := _MAP_FAILURE_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="failure",
            reason=match.group("reason"),
        )
    if match := _MAP_SKIP_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="skipped",
            reason=match.group("reason"),
        )
    if match := _MAP_POST_SKIP_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="skipped",
            sequence=int(match.group("sequence")),
            reason=match.group("reason"),
        )
    if match := _MAP_WARNING_RE.search(line):
        return UploadEvent(
            channel="connectivity",
            status="warning",
            reason=match.group("reason"),
        )

    if _DATALOG_SENDING_RE.search(line):
        return UploadEvent(channel="datalog", status="attempt")
    if match := _DATALOG_SUCCESS_RE.search(line):
        return UploadEvent(
            channel="datalog",
            status="success",
            reason=match.group("result") or None,
        )
    if _DATALOG_FAILURE_RE.search(line):
        return UploadEvent(channel="datalog", status="failure")
    if match := _DATALOG_WARNING_RE.search(line):
        return UploadEvent(
            channel="datalog",
            status="warning",
            reason=match.group("reason"),
        )

    return None
