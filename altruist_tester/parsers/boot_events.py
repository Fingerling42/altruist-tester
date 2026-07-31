"""Parser for firmware boot/reset telemetry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_BOOT_RE = re.compile(r"^\[BOOT\]\s+(?P<fields>.+?)\s*$")
_FIELD_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\S+)")


def parse_key_value_fields(text: str) -> dict[str, str]:
    """Parse space-separated ``key=value`` tokens from one firmware line."""

    return {
        match.group("key"): match.group("value") for match in _FIELD_RE.finditer(text)
    }


def _parse_int(fields: dict[str, str], key: str) -> int | None:
    value = fields.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_bool(fields: dict[str, str], key: str) -> bool | None:
    value = fields.get(key)
    if value is None:
        return None
    if value in {"0", "false", "False"}:
        return False
    if value in {"1", "true", "True"}:
        return True
    return None


@dataclass(frozen=True, slots=True)
class BootEvent:
    """Parsed firmware boot/reset context from one ``[BOOT]`` line."""

    reset_reason: str | None = None
    reset_code: int | None = None
    boot: int | None = None
    crash_valid: bool | None = None
    prev_uptime_sec: int | None = None
    prev_free_heap: int | None = None
    last_section_id: int | None = None
    last_section: str | None = None
    free_heap: int | None = None
    raw_fields: dict[str, str] | None = None

    def as_event_payload(self) -> dict[str, Any]:
        """Return boot context as a JSON-friendly event payload."""

        return {
            "reset_reason": self.reset_reason,
            "reset_code": self.reset_code,
            "boot": self.boot,
            "crash_valid": self.crash_valid,
            "prev_uptime_sec": self.prev_uptime_sec,
            "prev_free_heap": self.prev_free_heap,
            "last_section_id": self.last_section_id,
            "last_section": self.last_section,
            "free_heap": self.free_heap,
            "raw_fields": self.raw_fields or {},
        }


def boot_event_from_fields(fields: dict[str, str]) -> BootEvent:
    """Build a :class:`BootEvent` from parsed firmware key-value fields."""

    return BootEvent(
        reset_reason=fields.get("reset_reason"),
        reset_code=_parse_int(fields, "reset_code"),
        boot=_parse_int(fields, "boot"),
        crash_valid=_parse_bool(fields, "crash_valid"),
        prev_uptime_sec=_parse_int(fields, "prev_uptime"),
        prev_free_heap=_parse_int(fields, "prev_heap"),
        last_section_id=_parse_int(fields, "last_section_id"),
        last_section=fields.get("last_section"),
        free_heap=_parse_int(fields, "heap"),
        raw_fields=fields,
    )


def parse_boot_event(line: str) -> BootEvent | None:
    """Parse one current firmware ``[BOOT]`` line."""

    match = _BOOT_RE.match(line.strip())
    if match is None:
        return None

    fields = parse_key_value_fields(match.group("fields"))
    if not fields:
        return None
    return boot_event_from_fields(fields)
