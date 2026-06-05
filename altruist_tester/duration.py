"""Duration parsing helpers for CLI options."""

from __future__ import annotations

import re


class DurationParseError(ValueError):
    """Raised when a duration string cannot be parsed."""


_DURATION_RE = re.compile(r"^\s*(?P<value>\d+)\s*(?P<unit>[smh]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {
    "": 1,
    "s": 1,
    "m": 60,
    "h": 60 * 60,
}


def parse_duration_seconds(value: str) -> int:
    """Parse a duration like 30s, 10m, or 24h into seconds."""

    match = _DURATION_RE.match(value)
    if not match:
        raise DurationParseError(
            "Duration must be a positive integer followed by s, m, or h, "
                "for example 30s, 10m, or 24h."
        )

    amount = int(match.group("value"))
    if amount <= 0:
        raise DurationParseError("Duration must be greater than zero.")

    unit = match.group("unit").lower()
    return amount * _UNIT_SECONDS[unit]
