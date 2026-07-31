"""Subsystem health checks based on firmware ``[SUBSYSTEM]`` events."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from altruist_tester.rules.severity import severity_for_subsystem_event

SubsystemStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class SubsystemFinding:
    """One subsystem health finding."""

    status: SubsystemStatus
    level: str
    subsystem: str
    reason: str
    count: int
    message: str
    first_seen: str | None = None
    last_seen: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "level": self.level,
            "subsystem": self.subsystem,
            "reason": self.reason,
            "count": self.count,
            "message": self.message,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True, slots=True)
class SubsystemHealthReport:
    """Aggregate subsystem health report."""

    status: SubsystemStatus
    events_count: int
    warning_count: int
    failure_count: int
    by_subsystem: dict[str, int]
    by_reason: dict[str, int]
    findings: tuple[SubsystemFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when no subsystem event failed."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "events_count": self.events_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "by_subsystem": self.by_subsystem,
            "by_reason": self.by_reason,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _status_for_event(level: str, subsystem: str, reason: str) -> SubsystemStatus:
    return severity_for_subsystem_event(
        level=level,
        subsystem=subsystem,
        reason=reason,
    )


def _event_message(
    *,
    status: SubsystemStatus,
    level: str,
    subsystem: str,
    reason: str,
    count: int,
) -> str:
    return (
        f"{subsystem} subsystem emitted {count} {level} event(s) "
        f"for reason={reason}; status={status}"
    )


def check_subsystem_health(
    records: tuple[dict[str, object], ...],
) -> SubsystemHealthReport:
    """Evaluate parsed firmware subsystem events."""

    if not records:
        return SubsystemHealthReport(
            status="ok",
            events_count=0,
            warning_count=0,
            failure_count=0,
            by_subsystem={},
            by_reason={},
            findings=(),
            message="No subsystem events observed",
        )

    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for record in records:
        level = str(record.get("level") or "")
        subsystem = str(record.get("subsystem") or "")
        reason = str(record.get("reason") or "")
        if not level or not subsystem or not reason:
            continue
        grouped.setdefault((level, subsystem, reason), []).append(record)

    findings = []
    for (level, subsystem, reason), group_records in sorted(grouped.items()):
        status = _status_for_event(level, subsystem, reason)
        count = len(group_records)
        findings.append(
            SubsystemFinding(
                status=status,
                level=level,
                subsystem=subsystem,
                reason=reason,
                count=count,
                message=_event_message(
                    status=status,
                    level=level,
                    subsystem=subsystem,
                    reason=reason,
                    count=count,
                ),
                first_seen=str(group_records[0].get("ts") or "") or None,
                last_seen=str(group_records[-1].get("ts") or "") or None,
            )
        )

    failure_count = sum(1 for finding in findings if finding.status == "fail")
    warning_count = sum(1 for finding in findings if finding.status == "warn")
    status: SubsystemStatus
    if failure_count:
        status = "fail"
    elif warning_count:
        status = "warn"
    else:
        status = "ok"

    by_subsystem = Counter(str(record.get("subsystem")) for record in records)
    by_reason = Counter(str(record.get("reason")) for record in records)

    return SubsystemHealthReport(
        status=status,
        events_count=len(records),
        warning_count=warning_count,
        failure_count=failure_count,
        by_subsystem=dict(sorted(by_subsystem.items())),
        by_reason=dict(sorted(by_reason.items())),
        findings=tuple(findings),
        message=(
            f"Observed {len(records)} subsystem event(s): "
            f"{failure_count} failure group(s), {warning_count} warning group(s)"
        ),
    )
