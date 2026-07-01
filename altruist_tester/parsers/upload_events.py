"""Parsers for firmware upload status lines."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
_DATALOG_EXTRINSIC_ATTEMPT_RE = re.compile(r"\bExtrinsic Datalog:\s*size\s+\d+")
_DATALOG_EXTRINSIC_RESULT_RE = re.compile(
    r'\bExtrinsic result:\s*(?P<result>"?0x[0-9a-fA-F]+"?)',
)

_API_NAME_RE = re.compile(r"API Name:\s*(?P<name>.+)$")
_API_COUNT_SENDS_RE = re.compile(r"Count Sends:\s*(?P<count>\d+)")
_API_IS_OK_RE = re.compile(r"Is OK:\s*(?P<ok>Yes|No)")
_DATALOG_API_NAME = "Robonomics Datalog"


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
    if _DATALOG_EXTRINSIC_ATTEMPT_RE.search(line):
        return UploadEvent(channel="datalog", status="attempt")
    if match := _DATALOG_EXTRINSIC_RESULT_RE.search(line):
        return UploadEvent(
            channel="datalog",
            status="success",
            reason=match.group("result"),
        )

    return None


@dataclass(slots=True)
class UploadStatusStreamParser:
    """Parse multi-line firmware upload status blocks.

    Firmware development logs print API status as separate lines:
    ``API Name``, ``Count Sends``, ``Last Send Time``, and ``Is OK``. This
    parser turns Datalog count increases into upload outcomes while ignoring
    repeated status snapshots with unchanged counters.
    """

    _current_api_name: str | None = None
    _current_count_sends: int | None = None
    _last_counts: dict[UploadChannel, int] = field(default_factory=dict)
    _pending_explicit_datalog_outcomes: int = 0

    def record_explicit_event(self, event: UploadEvent | None) -> None:
        """Remember explicit Datalog outcomes to avoid status-block duplicates."""

        if event is None:
            return
        if event.channel == "datalog" and event.status in {"success", "failure"}:
            self._pending_explicit_datalog_outcomes += 1

    def feed(self, line: str) -> tuple[UploadEvent, ...]:
        """Parse one line and return upload events completed by it."""

        if match := _API_NAME_RE.search(line):
            self._current_api_name = match.group("name").strip()
            self._current_count_sends = None
            return ()

        if self._current_api_name is None:
            return ()

        if match := _API_COUNT_SENDS_RE.search(line):
            self._current_count_sends = int(match.group("count"))
            return ()

        if match := _API_IS_OK_RE.search(line):
            is_ok = match.group("ok") == "Yes"
            events = self._finish_api_status(is_ok=is_ok)
            self._current_api_name = None
            self._current_count_sends = None
            return events

        return ()

    def _finish_api_status(self, *, is_ok: bool) -> tuple[UploadEvent, ...]:
        if (
            self._current_api_name != _DATALOG_API_NAME
            or self._current_count_sends is None
        ):
            return ()

        count_sends = self._current_count_sends
        previous_count = self._last_counts.get("datalog")
        self._last_counts["datalog"] = count_sends
        if previous_count is None or count_sends <= previous_count:
            return ()

        delta = count_sends - previous_count
        if self._pending_explicit_datalog_outcomes:
            duplicate_count = min(delta, self._pending_explicit_datalog_outcomes)
            delta -= duplicate_count
            self._pending_explicit_datalog_outcomes -= duplicate_count
        if delta <= 0:
            return ()

        status: UploadStatus = "success" if is_ok else "failure"
        ok_text = "Yes" if is_ok else "No"
        reason = f"API status Count Sends={count_sends}, Is OK={ok_text}"
        return tuple(
            UploadEvent(
                channel="datalog",
                status=status,
                sequence=count_sends - offset,
                reason=reason,
            )
            for offset in range(delta - 1, -1, -1)
        )
