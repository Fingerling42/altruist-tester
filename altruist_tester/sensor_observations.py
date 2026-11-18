"""Versioned debug UART snapshots and measurement identity, independent of uplink."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from altruist_tester.samples import SensorSample
from altruist_tester.sensor_acceptance import (
    SensorAcceptanceLedger,
    acceptance_findings,
)
from altruist_tester.sensor_contract import metric_channel, parse_contract

MARKER = '"_observations"'
parse_sensor_observation = parse_contract


@dataclass(slots=True)
class SensorObservationTracker:
    """Keep only latest identities; snapshots do not promise every measurement."""

    capture_started_at: datetime | None = None
    acceptance: SensorAcceptanceLedger = field(default_factory=SensorAcceptanceLedger)
    expected_settings: dict | None = None
    profile_rules: dict = field(default_factory=dict)
    profile_error: dict | None = None
    selected: bool = False
    snapshots: int = 0
    duplicates: int = 0
    missed_snapshots: int = 0
    unobserved_updates: int = 0
    issues: Counter = field(default_factory=Counter)
    coverage: Counter = field(default_factory=Counter)
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
                self.duplicates += sum(m["seq"] > 0 for m in snapshot["metrics"])
                return snapshot, []
            if (
                snapshot["snapshot_seq"] <= previous["snapshot_seq"]
                or snapshot["time_ms"] < previous["time_ms"]
            ):
                self.issues["snapshot_regression"] += 1
                return {"invalid_reason": "snapshot_regression"}, []
        # Validate temporal evidence atomically before changing the accepted baseline.
        if previous:
            try:
                validate_transition(previous, snapshot)
            except ValueError as exc:
                self.issues[str(exc)] += 1
                return {"invalid_reason": str(exc)}, []
        if self.expected_settings is not None:
            expected = self.expected_settings.get(sensor)
            if expected != snapshot["settings"]:
                self.profile_error = {
                    "sensor": sensor,
                    "expected": expected,
                    "declared": snapshot["settings"],
                    "reason": "settings_mismatch",
                }
        if previous and snapshot["settings"] != previous["settings"]:
            self.profile_error = {
                "sensor": sensor,
                "expected": previous["settings"],
                "declared": snapshot["settings"],
                "reason": "settings_changed",
            }
        if self.profile_error:
            return {**snapshot, "profile_error": self.profile_error}, []
        current_metrics = {m["metric"] for m in snapshot["metrics"]}
        if any(s == sensor and m not in current_metrics for s, m in self.latest):
            self.issues["missing_metric"] += 1
        # Validate all identities before accepting any metric from this snapshot.
        for item in snapshot["metrics"]:
            prior = self.latest.get((sensor, item["metric"]))
            if prior and previous and item["seq"] > prior["seq"]:
                channel = metric_channel(sensor, item["metric"])
                delta = (
                    snapshot["channels"][channel]["ok_total"]
                    - previous["channels"][channel]["ok_total"]
                )
                if item["seq"] - prior["seq"] > delta:
                    self.issues["metric_cycle_conflict"] += 1
                    return {"invalid_reason": "metric_cycle_conflict"}, []
            if (
                prior
                and prior["seq"] > 0
                and (
                    item["seq"] < prior["seq"]
                    or (
                        item["measured_ms"] is None
                        or item["measured_ms"] < prior["measured_ms"]
                    )
                    or (
                        item["seq"] == prior["seq"]
                        and any(
                            item[k] != prior[k]
                            for k in ("value", "unit", "measured_ms")
                        )
                    )
                    or (
                        item["seq"] > prior["seq"]
                        and item["measured_ms"] <= prior["measured_ms"]
                    )
                )
            ):
                self.issues["measurement_regression"] += 1
                return {"invalid_reason": "measurement_regression"}, []
        if previous:
            self.missed_snapshots += (
                snapshot["snapshot_seq"] - previous["snapshot_seq"] - 1
            )
        if previous:
            delta = snapshot["failures"]["next_seq"] - previous["failures"]["next_seq"]
            visible = sum(
                r["seq"] >= previous["failures"]["next_seq"]
                for r in snapshot["failures"]["recent"]
            )
            if delta > visible:
                self.coverage["failure_tail_eviction"] += delta - visible
            if "noise" in snapshot:
                if snapshot["snapshot_seq"] > previous["snapshot_seq"] + 1:
                    self.coverage["noise_interval_loss"] += 1
                completed = (
                    snapshot["noise"]["window_current"]["seq"]
                    - previous["noise"]["window_current"]["seq"]
                )
                if completed > len(snapshot["noise"]["windows"]):
                    self.coverage["noise_window_loss"] += completed - len(
                        snapshot["noise"]["windows"]
                    )
        if self.acceptance.started_at is None:
            self.acceptance.started_at = self.capture_started_at
        self.acceptance.feed(snapshot, previous, received)
        self._snapshots[sensor] = snapshot
        samples = []
        for item in snapshot["metrics"]:
            key = sensor, item["metric"]
            prior = self.latest.get(key)
            is_new = item["seq"] > 0 and (prior is None or item["seq"] > prior["seq"])
            if prior and is_new:
                self.unobserved_updates += max(0, item["seq"] - prior["seq"] - 1)
            if not is_new and item["seq"] > 0:
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
                ).isoformat()
                if item["measured_ms"] is not None
                else None,
            }
            self.latest[key] = {
                **item,
                "sensor_state": snapshot["state"],
                "channel": metric_channel(sensor, item["metric"]),
                "age_seconds": (snapshot["time_ms"] - item["measured_ms"]) / 1000
                if item["measured_ms"] is not None
                else None,
                "received_ts": received.isoformat(),
            }
            if is_new and item["state"] == "ok":
                samples.append(
                    SensorSample(
                        sensor=sensor,
                        metric=item["metric"],
                        value=item["value"],
                        unit=item["unit"],
                        source="serial_observation_v2",
                        observation=observation,
                    )
                )
        return snapshot, samples

    def as_dict(self) -> dict:
        return {
            "acceptance": self.acceptance.as_dict(),
            "expected_settings": self.expected_settings,
            "profile_rules": self.profile_rules,
            "profile_error": self.profile_error,
            "selected": self.selected,
            "source": "serial_observation_v2",
            "snapshots": self.snapshots,
            "duplicates": self.duplicates,
            "missed_snapshots": self.missed_snapshots,
            "unobserved_updates": self.unobserved_updates,
            "issues": dict(self.issues),
            "coverage": dict(self.coverage),
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
) -> list[dict]:
    """Combine contract integrity and capture-wide acceptance evidence."""
    findings = []
    if not report.get("selected"):
        return [
            {
                "severity": "fail",
                "code": "SENSOR_OBSERVATION_MISSING",
                "message": "Required _observations UART telemetry was not observed",
                "rule": "sensor_observations",
            }
        ]
    for reason, count in report.get("issues", {}).items():
        findings.append(
            {
                "severity": "fail",
                "code": "SENSOR_OBSERVATION_" + reason.upper(),
                "message": f"Local sensor contract: {reason} ({count})",
                "rule": "sensor_observations",
            }
        )
    for reason, count in report.get("coverage", {}).items():
        findings.append(
            {
                "severity": "warn",
                "code": "SENSOR_OBSERVATION_COVERAGE",
                "message": f"Incomplete UART evidence: {reason} ({count})",
                "rule": "sensor_observations",
            }
        )
    findings.extend(acceptance_findings(report, reference_time))
    return findings


def validate_transition(previous: dict, current: dict) -> None:
    """Check counters and repeated bounded evidence across received snapshots."""

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    require(current["cycle_seq"] >= previous["cycle_seq"], "cycle_regression")
    cycle_delta = current["cycle_seq"] - previous["cycle_seq"]
    channel_deltas = [
        current["channels"][name]["cycles_total"] - c["cycles_total"]
        for name, c in previous["channels"].items()
    ]
    require(
        max(channel_deltas) <= cycle_delta <= sum(channel_deltas),
        "channel_cycle_delta_conflict",
    )
    for name, prior in previous["channels"].items():
        channel = current["channels"][name]
        for key in ("cycles_total", "ok_total", "failed_total"):
            require(channel[key] >= prior[key], "channel_counter_regression")
        for key, counter in (
            ("last_ok_ms", "ok_total"),
            ("last_failed_ms", "failed_total"),
        ):
            old, new = prior[key], channel[key]
            require(
                old is None or (new is not None and new >= old),
                "channel_time_regression",
            )
            require(
                channel[counter] != prior[counter] or new == old,
                "channel_time_without_cycle",
            )
            require(
                channel[counter] == prior[counter]
                or (new is not None and (old is None or new > old)),
                "channel_cycle_without_time",
            )
        old, new = prior["last_recovered_ms"], channel["last_recovered_ms"]
        require(old is None or (new is not None and new >= old), "recovery_regression")
        if new != old:
            require(
                channel["ok_total"] > prior["ok_total"] and new > previous["time_ms"],
                "recovery_without_success",
            )
        if prior["state"] == "error" and channel["state"] != "error":
            require(
                channel["ok_total"] > prior["ok_total"]
                and new is not None
                and new > previous["time_ms"],
                "error_cleared_without_success",
            )
    old_tail, new_tail = previous["failures"], current["failures"]
    require(new_tail["next_seq"] >= old_tail["next_seq"], "failure_sequence_regression")
    old_records = {r["seq"]: r for r in old_tail["recent"]}
    for record in new_tail["recent"]:
        if record["seq"] in old_records:
            require(record == old_records[record["seq"]], "failure_history_changed")
        elif record["seq"] >= old_tail["next_seq"]:
            require(
                record["at_ms"] >= previous["time_ms"]
                and record["cycle_seq"] > previous["cycle_seq"],
                "failure_before_baseline",
            )
    new_records = [r for r in new_tail["recent"] if r["seq"] >= old_tail["next_seq"]]
    delta = new_tail["next_seq"] - old_tail["next_seq"]
    deltas = []
    for name, channel in current["channels"].items():
        failed = channel["failed_total"] - previous["channels"][name]["failed_total"]
        visible = sum(name in r["channels"] for r in new_records)
        require(visible <= failed <= delta, "failure_delta_conflict")
        require(delta > len(new_records) or failed == visible, "failure_delta_conflict")
        deltas.append(failed)
    require(max(deltas) <= delta <= sum(deltas), "failure_delta_conflict")
    if "noise" not in current:
        return
    old, new = previous["noise"], current["noise"]
    require(old["started_ms"] == new["started_ms"], "noise_start_changed")
    for key in ("successful_blocks_total", "active_ms_total"):
        require(new[key] >= old[key], "noise_counter_regression")
    active_delta = new["active_ms_total"] - old["active_ms_total"]
    if new["successful_blocks_total"] == old["successful_blocks_total"]:
        require(
            new["interval"]["open_gap_active_ms"]
            == old["interval"]["open_gap_active_ms"] + active_delta,
            "noise_open_gap_regression",
        )
    if current["snapshot_seq"] == previous["snapshot_seq"] + 1:
        require(
            new["interval"]["max_gap_active_ms"]
            >= old["interval"]["open_gap_active_ms"],
            "noise_gap_lost",
        )
    p, q = old["sds_pause"], new["sds_pause"]
    require(
        q["cycle_seq"] >= p["cycle_seq"]
        and q["excluded_ms_total"] >= p["excluded_ms_total"],
        "pause_regression",
    )
    budget = (
        current["settings"]["sds_warmup_ms"] + current["settings"]["sds_collect_ms"]
    )
    # Unseen cycles cannot exclude more elapsed time or renew an old allowance.
    extra = q["excluded_ms_total"] - p["excluded_ms_total"]
    allowance = (q["cycle_seq"] - p["cycle_seq"]) * budget
    if p["state"] == "active":
        allowance += max(0, budget - (previous["time_ms"] - p["started_ms"]))
    require(
        extra <= min(current["time_ms"] - previous["time_ms"], allowance),
        "pause_budget_conflict",
    )
    if q["cycle_seq"] == p["cycle_seq"]:
        require(q["started_ms"] == p["started_ms"], "pause_start_changed")
        require(p["state"] != "idle" or q["state"] != "active", "pause_reentered")
    require(
        new["interval"]["start_ms"] >= previous["time_ms"], "noise_interval_overlap"
    )
    if current["snapshot_seq"] == previous["snapshot_seq"] + 1:
        require(
            new["interval"]["start_ms"] == previous["time_ms"], "noise_interval_missing"
        )
    prior_windows = {w["seq"]: w for w in old["windows"]}
    for window in new["windows"]:
        if window["seq"] in prior_windows:
            require(window == prior_windows[window["seq"]], "noise_window_changed")
    a, b = old["window_current"], new["window_current"]
    require(b["seq"] >= a["seq"], "noise_window_regression")
    success_delta = new["successful_blocks_total"] - old["successful_blocks_total"]
    if a["seq"] == b["seq"]:
        require(
            b["successful_blocks"] - a["successful_blocks"] == success_delta,
            "noise_success_delta_conflict",
        )
    elif any(w["seq"] == a["seq"] for w in new["windows"]):
        blocks = sum(
            w["successful_blocks"] for w in new["windows"] if w["seq"] >= a["seq"]
        )
        require(
            blocks + b["successful_blocks"] - a["successful_blocks"] == success_delta,
            "noise_success_delta_conflict",
        )
    if a["seq"] == b["seq"]:
        require(
            b["start_ms"] == a["start_ms"]
            and b["successful_blocks"] >= a["successful_blocks"],
            "noise_current_regression",
        )
    else:
        closed = next((w for w in new["windows"] if w["seq"] == a["seq"]), None)
        if closed:
            require(
                closed["start_ms"] == a["start_ms"]
                and closed["successful_blocks"] >= a["successful_blocks"],
                "noise_current_regression",
            )
