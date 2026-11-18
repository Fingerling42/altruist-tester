"""Versioned debug UART snapshots and measurement identity, independent of uplink."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from altruist_tester.samples import SensorSample

MARKER = '"_observations"'
MAX_LINE_BYTES = 8192
SENSORS = {"BME280", "BMP280", "BME680", "SCD4x", "SDS", "ICS43434"}


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**53 - 1


def parse_sensor_observation(line: str) -> dict[str, Any] | None:
    """Return a validated snapshot; raise on a claimed but invalid L3 record.

    This marker is reserved. Invalid L3 never falls back to legacy JSON parsing.
    Unknown fields are ignored, while all known fields are checked atomically.
    """
    if MARKER not in line:
        return None
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ValueError("line_too_long")
    try:
        data = json.loads(line[line.index("{") :])
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc
    meta = data.get("_observations") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or type(meta.get("version")) is not int:
        raise ValueError("missing_version")
    if meta["version"] != 1:
        raise ValueError("unknown_version")
    if not re.fullmatch(r"[0-9a-fA-F]{12}", str(meta.get("device_id", ""))):
        raise ValueError("invalid_device_id")
    if not re.fullmatch(
        r"[0-9a-fA-F]{1,8}-[0-9a-fA-F]{1,8}", str(meta.get("boot_id", ""))
    ):
        raise ValueError("invalid_boot_id")
    if not isinstance(meta.get("model"), str) or meta.get("model") not in {
        "urban",
        "insight",
    }:
        raise ValueError("invalid_model")
    sensor = meta.get("sensor")
    if not isinstance(sensor, str) or sensor not in SENSORS:
        raise ValueError("invalid_sensor")
    for key in ("snapshot_seq", "time_ms"):
        value = meta.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}", value):
            meta[key] = int(value)
    if not all(
        _integer(meta.get(k), minimum=m)
        for k, m in (("snapshot_seq", 1), ("time_ms", 0), ("errors", 0))
    ):
        raise ValueError("invalid_snapshot_clock")
    if meta["time_ms"] > 2**42 - 1:
        raise ValueError("invalid_snapshot_clock")
    if not isinstance(meta.get("state"), str) or meta.get("state") not in {
        "never",
        "ok",
        "error",
        "warming",
        "waiting",
    }:
        raise ValueError("invalid_state")
    if not re.fullmatch(r"[a-z0-9_]{1,64}", str(meta.get("reason", ""))):
        raise ValueError("invalid_reason")
    metrics = data.get(sensor)
    if not isinstance(metrics, dict) or len(metrics) > 16:
        raise ValueError("invalid_metrics")
    samples = []
    for metric, item in metrics.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", metric) or not isinstance(
            item, dict
        ):
            raise ValueError("invalid_metric")
        value = item.get("value")
        if metric == "voc_phrase" and isinstance(value, str):
            continue
        obs = item.get("observation")
        if not isinstance(obs, dict):
            raise ValueError("missing_metric_observation")
        for key in ("seq", "measured_ms"):
            value_clock = obs.get(key)
            if isinstance(value_clock, str) and re.fullmatch(
                r"[0-9]{1,16}", value_clock
            ):
                obs[key] = int(value_clock)
        if not _integer(obs.get("seq"), minimum=1) or not _integer(
            obs.get("measured_ms")
        ):
            raise ValueError("invalid_measurement_clock")
        if obs["measured_ms"] > meta["time_ms"]:
            raise ValueError("measurement_in_future")
        if not isinstance(obs.get("state"), str) or obs.get("state") not in {
            "ok",
            "error",
            "warming",
            "waiting",
        }:
            raise ValueError("invalid_metric_state")
        # Null/invalid cached values are allowed only as failed observations.
        if (
            type(value) not in {int, float}
            or (type(value) is int and abs(value) > 2**53 - 1)
            or not math.isfinite(value)
        ):
            if obs["state"] == "ok":
                raise ValueError("invalid_value")
            value = None
        unit = item.get("units")
        if unit is not None and (not isinstance(unit, str) or len(unit) > 24):
            raise ValueError("invalid_unit")
        samples.append(
            {
                "metric": metric,
                "value": value,
                "unit": unit,
                **{k: obs[k] for k in ("seq", "measured_ms", "state")},
            }
        )
    return {
        **{
            k: meta[k]
            for k in (
                "version",
                "device_id",
                "boot_id",
                "model",
                "sensor",
                "snapshot_seq",
                "time_ms",
                "state",
                "errors",
                "reason",
            )
        },
        "metrics": samples,
    }


@dataclass(slots=True)
class SensorObservationTracker:
    """Keep only latest identities; snapshots do not promise every measurement."""

    selected: bool = False
    snapshots: int = 0
    duplicates: int = 0
    missed_snapshots: int = 0
    unobserved_updates: int = 0
    issues: Counter = field(default_factory=Counter)
    latest: dict[tuple[str, str], dict] = field(default_factory=dict)
    _snapshots: dict[str, dict] = field(default_factory=dict)
    _origins: dict[str, datetime] = field(default_factory=dict)
    _device: str | None = None
    _model: str | None = None
    _boot: str | None = None
    _retired_boots: set[str] = field(default_factory=set)

    def feed(
        self, line: str, received: datetime
    ) -> tuple[dict | None, list[SensorSample]]:
        if MARKER not in line:
            return None, []
        self.selected = True
        try:
            snapshot = parse_sensor_observation(line)
        except ValueError as exc:
            self.issues[str(exc)] += 1
            return {"invalid_reason": str(exc)}, []
        assert snapshot is not None
        self.snapshots += 1
        device = snapshot["device_id"].upper()
        boot = snapshot["boot_id"]
        if self._device is not None and (
            device != self._device or snapshot["model"] != self._model
        ):
            self.issues["identity_conflict"] += 1
            return {"invalid_reason": "identity_conflict"}, []
        self._device, self._model = device, snapshot["model"]
        if boot in self._retired_boots:
            self.issues["retired_boot"] += 1
            return {"invalid_reason": "retired_boot"}, []
        if self._boot is not None and boot != self._boot:
            self.issues["reboot"] += 1
            self._retired_boots.add(self._boot)
            self.latest.clear()
            self._snapshots.clear()
        self._boot = boot
        self._origins.setdefault(
            boot, received - timedelta(milliseconds=snapshot["time_ms"])
        )
        sensor = snapshot["sensor"]
        previous = self._snapshots.get(sensor)
        if previous:
            if (
                snapshot["snapshot_seq"] == previous["snapshot_seq"]
                and snapshot == previous
            ):
                self.duplicates += len(snapshot["metrics"])
                return snapshot, []
            if (
                snapshot["snapshot_seq"] <= previous["snapshot_seq"]
                or snapshot["time_ms"] < previous["time_ms"]
                or snapshot["errors"] < previous["errors"]
            ):
                self.issues["snapshot_regression"] += 1
                return {"invalid_reason": "snapshot_regression"}, []
        current_metrics = {m["metric"] for m in snapshot["metrics"]}
        if any(s == sensor and m not in current_metrics for s, m in self.latest):
            self.issues["missing_metric"] += 1
        # Validate all identities before accepting any metric from this snapshot.
        for item in snapshot["metrics"]:
            prior = self.latest.get((sensor, item["metric"]))
            if prior and (
                item["seq"] < prior["seq"]
                or item["measured_ms"] < prior["measured_ms"]
                or (
                    item["seq"] == prior["seq"]
                    and any(
                        item[k] != prior[k] for k in ("value", "unit", "measured_ms")
                    )
                )
                or (
                    item["seq"] > prior["seq"]
                    and item["measured_ms"] <= prior["measured_ms"]
                )
            ):
                self.issues["measurement_regression"] += 1
                return {"invalid_reason": "measurement_regression"}, []
        if previous:
            self.missed_snapshots += (
                snapshot["snapshot_seq"] - previous["snapshot_seq"] - 1
            )
        self._snapshots[sensor] = snapshot
        samples = []
        for item in snapshot["metrics"]:
            key = sensor, item["metric"]
            prior = self.latest.get(key)
            is_new = prior is None or item["seq"] > prior["seq"]
            if prior and is_new:
                self.unobserved_updates += item["seq"] - prior["seq"] - 1
            if not is_new:
                self.duplicates += 1
            observation = {
                "device_id": device,
                "boot_id": boot,
                "snapshot_ms": snapshot["time_ms"],
                "seq": item["seq"],
                "measured_ms": item["measured_ms"],
                "state": item["state"],
                "measured_ts": (
                    self._origins[boot] + timedelta(milliseconds=item["measured_ms"])
                ).isoformat(),
            }
            self.latest[key] = {
                **item,
                "sensor_state": snapshot["state"],
                "errors": snapshot["errors"],
                "age_seconds": (snapshot["time_ms"] - item["measured_ms"]) / 1000,
                "received_ts": received.isoformat(),
            }
            if is_new and item["state"] == "ok" and snapshot["state"] == "ok":
                samples.append(
                    SensorSample(
                        sensor=sensor,
                        metric=item["metric"],
                        value=item["value"],
                        unit=item["unit"],
                        source="serial_observation_v1",
                        observation=observation,
                    )
                )
        return snapshot, samples

    def as_dict(self) -> dict:
        return {
            "selected": self.selected,
            "source": "serial_observation_v1" if self.selected else "legacy",
            "snapshots": self.snapshots,
            "duplicates": self.duplicates,
            "missed_snapshots": self.missed_snapshots,
            "unobserved_updates": self.unobserved_updates,
            "issues": dict(self.issues),
            "sensors": [
                {k: v for k, v in snapshot.items() if k != "metrics"}
                for snapshot in self._snapshots.values()
            ],
            "latest": [
                {"sensor": s, "metric": m, **v}
                for (s, m), v in sorted(self.latest.items())
            ],
        }


def observation_findings(
    report: dict,
    *,
    reference_time: datetime | None,
    interval: int,
    warn_after: int,
    fail_after: int,
) -> list[dict]:
    """Apply tester freshness thresholds, never firmware-reported PASS labels."""
    findings = []
    if not report.get("selected"):
        return findings
    for reason, count in report.get("issues", {}).items():
        findings.append(
            {
                "severity": "fail",
                "code": "SENSOR_OBSERVATION_" + reason.upper(),
                "message": f"Local sensor contract: {reason} ({count})",
                "rule": "sensor_observations",
            }
        )
    for sensor in report.get("sensors", []):
        if sensor["errors"] or sensor["state"] == "error":
            severity = "fail" if sensor["state"] == "error" else "warn"
            findings.append(
                {
                    "severity": severity,
                    "code": "SENSOR_OBSERVATION_READ_ERRORS",
                    "message": (
                        f"{sensor['sensor']}: state={sensor['state']}, "
                        f"cumulative errors={sensor['errors']}, "
                        f"reason={sensor['reason']}"
                    ),
                    "rule": "sensor_observations",
                }
            )
    for item in report.get("latest", []):
        age = item["age_seconds"]
        if reference_time:
            age += max(
                0,
                (
                    reference_time - datetime.fromisoformat(item["received_ts"])
                ).total_seconds(),
            )
        status = (
            "fail"
            if age >= interval * fail_after
            else "warn"
            if age >= interval * warn_after
            else None
        )
        message = (
            f"{item['sensor']}.{item['metric']}: "
            f"last successful measurement age {age:.1f}s"
        )
        if item["state"] == "error" or item["sensor_state"] == "error":
            status = "fail"
            message += "; reading failed; cached value is not a new measurement"
        elif item["state"] != "ok":
            status = status or "warn"
            message += f"; state={item['state']}"
        if status:
            findings.append(
                {
                    "severity": status,
                    "code": "SENSOR_OBSERVATION_FRESHNESS",
                    "message": message,
                    "rule": "sensor_observations",
                }
            )
    return findings
