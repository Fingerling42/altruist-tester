"""Runtime counter checks for development metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

RuntimeStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class RuntimeCounterFinding:
    """One monotonic counter violation."""

    status: RuntimeStatus
    code: str
    previous_index: int
    current_index: int
    previous_value: int
    current_value: int
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "code": self.code,
            "previous_index": self.previous_index,
            "current_index": self.current_index,
            "previous_value": self.previous_value,
            "current_value": self.current_value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCounterReport:
    """Aggregate runtime monotonic counter check result."""

    status: RuntimeStatus
    checked_records_count: int
    initial_boot: int | None
    last_boot: int | None
    min_uptime_sec: int | None
    max_uptime_sec: int | None
    failure_count: int
    findings: tuple[RuntimeCounterFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the runtime counter check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "checked_records_count": self.checked_records_count,
            "initial_boot": self.initial_boot,
            "last_boot": self.last_boot,
            "min_uptime_sec": self.min_uptime_sec,
            "max_uptime_sec": self.max_uptime_sec,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_values(
    records: Sequence[dict[str, object]],
    field: str,
) -> list[tuple[int, int]]:
    values = []
    for index, record in enumerate(records):
        value = _optional_int(record.get(field))
        if value is not None:
            values.append((index, value))
    return values


def _check_non_decreasing(
    values: list[tuple[int, int]],
    *,
    code: str,
    label: str,
) -> list[RuntimeCounterFinding]:
    findings = []
    for (previous_index, previous), (current_index, current) in zip(
        values,
        values[1:],
        strict=False,
    ):
        if current < previous:
            findings.append(
                RuntimeCounterFinding(
                    status="fail",
                    code=code,
                    previous_index=previous_index,
                    current_index=current_index,
                    previous_value=previous,
                    current_value=current,
                    message=f"{label} decreased from {previous} to {current}",
                )
            )
    return findings


def _check_non_increasing(
    values: list[tuple[int, int]],
    *,
    code: str,
    label: str,
) -> list[RuntimeCounterFinding]:
    findings = []
    for (previous_index, previous), (current_index, current) in zip(
        values,
        values[1:],
        strict=False,
    ):
        if current > previous:
            findings.append(
                RuntimeCounterFinding(
                    status="fail",
                    code=code,
                    previous_index=previous_index,
                    current_index=current_index,
                    previous_value=previous,
                    current_value=current,
                    message=f"{label} increased from {previous} to {current}",
                )
            )
    return findings


def check_runtime_counters(
    records: Sequence[dict[str, object]],
) -> RuntimeCounterReport:
    """Check monotonic uptime and boot counter values.

    Uptime must not decrease during one run, and the firmware boot counter is
    expected to stay constant. Missing development metrics produce a warning
    because runtime continuity cannot be verified.
    """

    boot_values = _metric_values(records, "boot")
    uptime_values = _metric_values(records, "uptime_sec")
    findings = [
        *_check_non_decreasing(
            uptime_values,
            code="UPTIME_DECREASED",
            label="uptime",
        ),
        *_check_non_increasing(
            boot_values,
            code="BOOT_COUNTER_INCREASED",
            label="boot counter",
        ),
    ]

    initial_boot = boot_values[0][1] if boot_values else None
    last_boot = boot_values[-1][1] if boot_values else None
    uptime_numbers = [value for _index, value in uptime_values]
    min_uptime_sec = min(uptime_numbers) if uptime_numbers else None
    max_uptime_sec = max(uptime_numbers) if uptime_numbers else None

    if findings:
        status: RuntimeStatus = "fail"
        message = f"{len(findings)} runtime counter checks failed"
    elif not records:
        status = "warn"
        message = "No development metrics were available for runtime checks"
    else:
        status = "ok"
        message = "Runtime counters remained monotonic"
        if initial_boot is not None and initial_boot > 1:
            message = f"{message}; initial boot counter was {initial_boot}"

    return RuntimeCounterReport(
        status=status,
        checked_records_count=len(records),
        initial_boot=initial_boot,
        last_boot=last_boot,
        min_uptime_sec=min_uptime_sec,
        max_uptime_sec=max_uptime_sec,
        failure_count=len(findings),
        findings=tuple(findings),
        message=message,
    )
