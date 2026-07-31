"""Keyword alert detection for serial logs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KeywordRule:
    """One serial keyword rule.

    Rules are matched case-insensitively against decoded UART lines.
    """

    code: str
    keyword: str
    pattern: re.Pattern[str]
    severity: str = "fail"


@dataclass(frozen=True, slots=True)
class KeywordAlert:
    """Detected runtime alert from one serial line."""

    code: str
    keyword: str
    severity: str
    line: str

    def as_event_payload(self) -> dict[str, str]:
        """Return alert as an event payload."""

        return {
            "code": self.code,
            "keyword": self.keyword,
            "severity": self.severity,
            "line": self.line,
        }


def _rule(
    code: str,
    keyword: str,
    pattern: str,
    *,
    severity: str = "fail",
) -> KeywordRule:
    return KeywordRule(
        code=code,
        keyword=keyword,
        pattern=re.compile(pattern, re.IGNORECASE),
        severity=severity,
    )


# Rules are anchored to Altruist firmware serial output, with ESP runtime crash
# patterns kept for failures printed below the firmware layer.
KEYWORD_RULES = (
    _rule("PANIC", "panic", r"\bpanic\b"),
    _rule("WATCHDOG", "watchdog", r"\bwatchdog\b|wdt"),
    _rule("BROWNOUT", "Brownout", r"\bbrownout\b"),
    _rule("POWER_GLITCH", "power glitch", r"\bpower glitch\b"),
    _rule("CPU_LOCKUP", "CPU lock-up", r"\bcpu lock[- ]?up\b"),
    _rule("EFUSE_ERROR", "eFuse error", r"\befuse error\b"),
    _rule("GURU_MEDITATION", "Guru Meditation", r"\bguru meditation\b"),
    _rule("ABORT", "abort", r"\babort(?:ed)?\b"),
    _rule("ASSERT_FAILED", "assert failed", r"\bassert failed\b"),
    _rule("STACK_CANARY", "stack canary", r"\bstack canary\b"),
    _rule("HEAP_CORRUPTION", "heap corruption", r"\bheap corruption\b"),
    _rule("LOAD_ACCESS_FAULT", "Load access fault", r"\bload access fault\b"),
    _rule("STORE_ACCESS_FAULT", "Store access fault", r"\bstore access fault\b"),
    _rule("UNEXPECTED_RESTART", "Unexpected restart", r"\bunexpected restart\b"),
)


def detect_keyword_alerts(
    line: str,
    rules: Iterable[KeywordRule] = KEYWORD_RULES,
) -> list[KeywordAlert]:
    """Detect runtime-alert keywords in one serial line.

    :param line: Decoded serial line.
    :param rules: Keyword rules to evaluate. The default set is based on
        Altruist firmware messages and ESP runtime crash output.
    :returns: All alerts matched in the line. A single line can produce more
        than one alert when it contains overlapping failure terms.
    """

    # Test firmware can print the injected sample name before printing the
    # actual sample; that metadata line must not trigger its own alert.
    if line.startswith("[TEST] Injecting serial sample:"):
        return []

    return [
        KeywordAlert(
            code=rule.code,
            keyword=rule.keyword,
            severity=rule.severity,
            line=line,
        )
        for rule in rules
        if rule.pattern.search(line)
    ]
