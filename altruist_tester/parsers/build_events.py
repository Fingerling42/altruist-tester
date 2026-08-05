"""Parser for firmware build identity lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from altruist_tester.parsers.boot_events import parse_key_value_fields

_BUILD_RE = re.compile(r"^\[BUILD\]\s+(?P<fields>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class BuildEvent:
    """Parsed firmware build identity from one ``[BUILD]`` line."""

    version: str | None = None
    channel: str | None = None
    commit: str | None = None
    model: str | None = None
    target: str | None = None
    language: str | None = None
    profile: str | None = None
    raw_fields: dict[str, str] | None = None

    def as_event_payload(self) -> dict[str, Any]:
        """Return build identity as a JSON-friendly event payload."""

        return {
            "version": self.version,
            "channel": self.channel,
            "commit": self.commit,
            "model": self.model,
            "target": self.target,
            "language": self.language,
            "profile": self.profile,
            "raw_fields": self.raw_fields or {},
        }


def build_event_from_fields(fields: dict[str, str]) -> BuildEvent:
    """Build a :class:`BuildEvent` from parsed firmware key/value fields."""

    return BuildEvent(
        version=fields.get("version"),
        channel=fields.get("channel"),
        commit=fields.get("commit"),
        model=fields.get("model"),
        target=fields.get("target"),
        language=fields.get("language"),
        profile=fields.get("profile"),
        raw_fields=fields,
    )


def parse_build_event(line: str) -> BuildEvent | None:
    """Parse one current firmware ``[BUILD]`` line."""

    match = _BUILD_RE.match(line.strip())
    if match is None:
        return None

    fields = parse_key_value_fields(match.group("fields"))
    if not fields:
        return None
    return build_event_from_fields(fields)
