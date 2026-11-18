"""Common sensor sample model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SampleKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class SensorSample:
    """One sensor metric value before it is timestamped in a run.

    Parser functions return this lightweight model. Artifact writers attach the
    run timestamp and turn it into ``SensorSampleRecord``.
    """

    sensor: str
    metric: str
    value: float
    unit: str | None
    source: str = "serial"
    observation: dict[str, Any] | None = None

    @property
    def key(self) -> SampleKey:
        """Return the time-series key for this sample."""

        return (self.sensor, self.metric)

    def as_payload(self) -> dict[str, object]:
        """Return the sample payload without timestamp."""

        return {
            "sensor": self.sensor,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            **(
                {"observation": self.observation}
                if self.observation is not None
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class SensorSampleRecord:
    """One timestamped sensor metric value stored in run artifacts."""

    ts: str
    sensor: str
    metric: str
    value: float
    unit: str | None
    source: str
    observation: dict[str, Any] | None = None

    @property
    def key(self) -> SampleKey:
        """Return the time-series key for this sample."""

        return (self.sensor, self.metric)

    @property
    def measurement_timestamp(self) -> str:
        return (
            self.ts
            if self.observation is None
            else self.observation.get("measured_ts", self.ts)
        )

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SensorSampleRecord:
        """Build a record from JSON-friendly sample data."""

        return cls(
            ts=str(value["ts"]),
            sensor=str(value["sensor"]),
            metric=str(value["metric"]),
            value=float(value["value"]),
            unit=None if value.get("unit") is None else str(value["unit"]),
            source=str(value["source"]),
            observation=value.get("observation"),
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly record."""

        return {
            "ts": self.ts,
            "sensor": self.sensor,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            **(
                {"observation": self.observation}
                if self.observation is not None
                else {}
            ),
        }


@dataclass(slots=True)
class SensorSampleSeries:
    """In-memory sample time series grouped by sensor and metric.

    Rule checks use this structure to evaluate presence, flatlines, and update
    cadence after capture completes.
    """

    by_key: dict[SampleKey, list[SensorSampleRecord]] = field(default_factory=dict)

    def append(self, sample: SensorSampleRecord) -> None:
        """Append one sample record to its time series."""

        self.by_key.setdefault(sample.key, []).append(sample)

    def count(self) -> int:
        """Return total number of records across all time series."""

        return sum(len(records) for records in self.by_key.values())

    def latest(self, key: SampleKey) -> SensorSampleRecord | None:
        """Return the latest sample for a key, if present."""

        records = self.by_key.get(key)
        if not records:
            return None
        return records[-1]
