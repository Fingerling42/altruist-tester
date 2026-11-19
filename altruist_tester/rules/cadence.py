"""Sensor update cadence checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CadenceStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class SensorCadenceFinding:
    """Result for one sensor metric update cadence check."""

    status: CadenceStatus
    sensor: str
    metric: str
    canonical_metric: str
    samples_count: int
    max_gap_seconds: float | None
    tail_gap_seconds: float | None
    expected_interval_seconds: int
    warn_after_seconds: int
    fail_after_seconds: int
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "sensor": self.sensor,
            "metric": self.metric,
            "canonical_metric": self.canonical_metric,
            "samples_count": self.samples_count,
            "max_gap_seconds": self.max_gap_seconds,
            "tail_gap_seconds": self.tail_gap_seconds,
            "expected_interval_seconds": self.expected_interval_seconds,
            "warn_after_seconds": self.warn_after_seconds,
            "fail_after_seconds": self.fail_after_seconds,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SensorCadenceReport:
    """Aggregate update cadence result for all sensor metric series."""

    status: CadenceStatus
    checked_series_count: int
    warning_count: int
    failure_count: int
    findings: tuple[SensorCadenceFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the cadence check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "checked_series_count": self.checked_series_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }
