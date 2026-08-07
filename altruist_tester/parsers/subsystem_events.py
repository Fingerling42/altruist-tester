"""Parser for firmware subsystem health events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from altruist_tester.parsers.boot_events import parse_key_value_fields

SubsystemEventLevel = Literal["event", "error"]

_SUBSYSTEM_RE = re.compile(
    r"^\[SUBSYSTEM\]\s+(?P<level>event|error)\s+(?P<fields>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SubsystemEvent:
    """Parsed firmware subsystem event from one ``[SUBSYSTEM]`` line."""

    level: SubsystemEventLevel
    subsystem: str
    reason: str
    details: dict[str, str]
    line: str

    def as_event_payload(self) -> dict[str, Any]:
        """Return event as a JSON-friendly payload."""

        return {
            "level": self.level,
            "subsystem": self.subsystem,
            "reason": self.reason,
            "details": self.details,
            "line": self.line,
        }


def parse_subsystem_event(line: str) -> SubsystemEvent | None:
    """Parse one current firmware ``[SUBSYSTEM]`` line."""

    stripped_line = line.strip()
    match = _SUBSYSTEM_RE.match(stripped_line)
    if match is None:
        return None

    fields = parse_key_value_fields(match.group("fields"))
    subsystem = fields.get("subsystem")
    reason = fields.get("reason")
    if not subsystem or not reason:
        return None

    level = cast(SubsystemEventLevel, match.group("level").lower())
    return SubsystemEvent(
        level=level,
        subsystem=subsystem,
        reason=reason,
        details={
            key: value
            for key, value in fields.items()
            if key not in {"subsystem", "reason"}
        },
        line=stripped_line,
    )
