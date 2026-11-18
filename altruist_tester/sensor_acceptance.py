"""Capture evidence for UART v2 timing, failure budgets and audio coverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from altruist_tester.rules.cadence import SensorCadenceFinding, SensorCadenceReport
from altruist_tester.rules.presence import canonical_metric_name

DAY_MS = 86400000


def finding(severity: str, code: str, message: str) -> dict:
    return dict(
        severity=severity, code=code, message=message, rule="sensor_observations"
    )


def elapsed(reference: datetime | None, received: str) -> float:
    return (
        max(0, (reference - datetime.fromisoformat(received)).total_seconds())
        if reference
        else 0
    )


@dataclass(slots=True)
class SensorAcceptanceLedger:
    """Retain bounded evidence; recovery never clears a capture finding."""

    started_at: datetime | None = None
    sensors: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)

    def note(self, key: tuple, severity: str, code: str, message: str) -> None:
        previous = self.findings.get(key)
        if previous is None or previous["severity"] != "fail":
            self.findings[key] = finding(severity, code, message)

    def feed(self, snapshot: dict, previous: dict | None, received: datetime) -> None:
        if self.started_at is None:
            self.started_at = received
        sensor, boot = snapshot["sensor"], snapshot["boot_id"]
        now = snapshot["time_ms"]
        key = boot, sensor
        first = key not in self.sensors
        state = self.sensors.setdefault(
            key,
            dict(
                sensor=sensor,
                boot_id=boot,
                baseline_ms=now,
                estimated_start_ms=now
                - max(0, (received - self.started_at).total_seconds()) * 1000,
                baseline_failed={
                    k: c["failed_total"] for k, c in snapshot["channels"].items()
                },
                failure_reasons=[],
                metrics={},
                channels={},
                full_noise_windows=0,
                last_window_seq=0,
            ),
        )
        state["failure_reasons"] = sorted(
            set(state["failure_reasons"])
            | {r["reason"] for r in snapshot["failures"]["recent"]}
            | {
                c["reason"]
                for c in snapshot["channels"].values()
                if c["state"] == "error"
            }
        )
        since_start = max(0, (received - self.started_at).total_seconds())
        # A first receipt only estimates device time at capture start. Failures
        # on/after that boundary are uncertain until a counter baseline exists.
        estimated_start = state["estimated_start_ms"]
        for name, channel in snapshot["channels"].items():
            ledger = state["channels"].setdefault(
                name,
                dict(
                    capture_failed=0,
                    historical_failed=0,
                    uncertain_failed=0,
                    recent_failure_ms=[],
                    budget_exceeded=False,
                    budget_evidence=None,
                ),
            )
            prefix = f"{sensor}.{name}"
            if first and channel["failed_total"]:
                historical = sum(
                    name in r["channels"] and r["at_ms"] < estimated_start
                    for r in snapshot["failures"]["recent"]
                )
                # An evicted prefix is historical only when even the newest
                # failure predates the estimated capture boundary.
                if channel["last_failed_ms"] < estimated_start:
                    historical = channel["failed_total"]
                ledger["historical_failed"] = historical
                ledger["uncertain_failed"] = channel["failed_total"] - historical
                if historical:
                    self.note(
                        (key, name, "history"),
                        "warn",
                        "SENSOR_ERROR_HISTORY",
                        f"{prefix}: {historical} historical failed cycle(s); "
                        "outside capture budget",
                    )
                if ledger["uncertain_failed"]:
                    self.note(
                        (key, name, "uncertain"),
                        "warn",
                        "SENSOR_ERROR_COVERAGE",
                        f"{prefix}: failure time at capture boundary is uncertain",
                    )
            if previous:
                delta = (
                    channel["failed_total"] - previous["channels"][name]["failed_total"]
                )
                ledger["capture_failed"] += delta
                records = [
                    r
                    for r in snapshot["failures"]["recent"]
                    if r["seq"] >= previous["failures"]["next_seq"]
                    and name in r["channels"]
                ]
                times = ledger["recent_failure_ms"]
                for record in records:
                    at = record["at_ms"]
                    times[:] = [t for t in times if at - t <= DAY_MS]
                    times.append(at)
                    if len(times) >= 2 and not ledger["budget_exceeded"]:
                        ledger["budget_exceeded"] = True
                        ledger["budget_evidence"] = {"failure_ms": list(times[-2:])}
                    # Two exact times suffice once the sticky failure is known.
                    times[:] = times[-2:]
                if delta > len(records):
                    self.note(
                        (key, name, "eviction"),
                        "warn",
                        "SENSOR_ERROR_COVERAGE",
                        f"{prefix}: {delta - len(records)} failed cycle time(s) "
                        "lost from UART tail",
                    )
                    span = now - previous["time_ms"]
                    # With no pair <=24h, n events span strictly >(n-1)*24h.
                    if (
                        delta >= 2
                        and span <= (delta - 1) * DAY_MS
                        and not ledger["budget_exceeded"]
                    ):
                        ledger["budget_exceeded"] = True
                        ledger["budget_evidence"] = dict(
                            interval_start_ms=previous["time_ms"],
                            interval_end_ms=now,
                            failed_delta=delta,
                        )
            if ledger["budget_exceeded"]:
                self.note(
                    (key, name, "budget"),
                    "fail",
                    "SENSOR_ERROR_BUDGET",
                    f"{prefix}: at least two failed cycles within rolling 24h; "
                    "failure retained",
                )
            elif ledger["capture_failed"]:
                self.note(
                    (key, name, "cycles"),
                    "warn",
                    "SENSOR_READ_RECOVERED",
                    f"{prefix}: {ledger['capture_failed']} capture failed cycle(s); "
                    "recovery required",
                )
            # Init failures do not invent measurement cycles, but their observed
            # recovery still cannot erase the diagnostic history.
            if channel["state"] == "error":
                self.note(
                    (key, name, "observed_error"),
                    "warn",
                    "SENSOR_READ_ERROR_OBSERVED",
                    f"{prefix}: observed error ({channel['reason']})",
                )
        if sensor == "ICS43434":
            self._noise(snapshot, state, key, first)
        else:
            for item in snapshot["metrics"]:
                metric = state["metrics"].setdefault(
                    item["metric"],
                    dict(
                        successes=0,
                        last_seq=0,
                        last_measured_ms=None,
                        first_wait_seconds=None,
                        max_gap_seconds=0,
                        unobserved_intervals=0,
                    ),
                )
                measured = item["measured_ms"]
                if measured is not None:
                    age = (now - measured) / 1000
                    metric["max_gap_seconds"] = max(metric["max_gap_seconds"], age)
                if item["seq"] > metric["last_seq"]:
                    old = metric["last_measured_ms"]
                    if old is not None:
                        if item["seq"] == metric["last_seq"] + 1:
                            metric["max_gap_seconds"] = max(
                                metric["max_gap_seconds"], (measured - old) / 1000
                            )
                        else:
                            # Missing UART updates cannot prove a physical
                            # inter-measurement gap, or establish full coverage.
                            metric["unobserved_intervals"] += 1
                    if item["state"] == "ok" and measured >= estimated_start:
                        metric["successes"] += 1
                        if metric["first_wait_seconds"] is None:
                            metric["first_wait_seconds"] = max(
                                0, since_start - (now - measured) / 1000
                            )
                    metric["last_seq"] = item["seq"]
                    metric["last_measured_ms"] = measured
        state["snapshot_ms"] = now
        state["received_ts"] = received.isoformat()
        state["elapsed_seconds"] = since_start

    def _noise(self, snapshot: dict, state: dict, key: tuple, first: bool) -> None:
        noise = snapshot["noise"]
        baseline = state["baseline_ms"]
        for window in noise["windows"]:
            if window["seq"] <= state["last_window_seq"]:
                continue
            state["last_window_seq"] = window["seq"]
            if window["start_ms"] < baseline:
                continue
            state["full_noise_windows"] += 1
            hz = window["successful_blocks"] / 300
            if hz < 2:
                self.note(
                    (key, "rate"),
                    "fail" if hz < 1 else "warn",
                    "SENSOR_NOISE_RATE",
                    f"ICS43434: complete 300s active window frequency {hz:g} Hz "
                    "(WARN <2, FAIL <1)",
                )
        interval = noise["interval"]
        gap = interval["open_gap_active_ms"]
        if not first and interval["max_gap_end_ms"] > baseline:
            gap = max(gap, interval["max_gap_active_ms"])
        if gap >= 60000:
            self.note(
                (key, "gap"),
                "fail" if gap >= 300000 else "warn",
                "SENSOR_NOISE_GAP",
                f"ICS43434: successful audio block gap {gap / 1000:g}s active "
                "(WARN >=60, FAIL >=300)",
            )

    def as_dict(self) -> dict:
        return dict(
            started_at=self.started_at.isoformat() if self.started_at else None,
            sensors=list(self.sensors.values()),
            findings=list(self.findings.values()),
        )


def acceptance_findings(report: dict, reference_time: datetime | None) -> list[dict]:
    ledger = report.get("acceptance", {})
    findings = list(ledger.get("findings", []))
    evidence = {(s["boot_id"], s["sensor"]): s for s in ledger.get("sensors", [])}
    for metric in report.get("latest", []):
        if metric["state"] == "error":
            findings.append(
                finding(
                    "fail",
                    "SENSOR_METRIC_UNRECOVERED",
                    f"{metric['sensor']}.{metric['metric']}: unrecovered metric error",
                )
            )
    for snapshot in report.get("sensors", []):
        sensor = snapshot["sensor"]
        for name, channel in snapshot["channels"].items():
            if channel["state"] == "error":
                findings.append(
                    finding(
                        "fail",
                        "SENSOR_READ_UNRECOVERED",
                        f"{sensor}.{name}: unrecovered error ({channel['reason']})",
                    )
                )
        if sensor != "ICS43434":
            continue
        state = evidence.get((snapshot["boot_id"], sensor))
        if state is None:
            continue
        noise = snapshot["noise"]
        tail_ms = elapsed(reference_time, state["received_ts"]) * 1000
        pause = noise["sds_pause"]
        remaining = 0
        if pause["state"] == "active":
            budget = (
                snapshot["settings"]["sds_warmup_ms"]
                + snapshot["settings"]["sds_collect_ms"]
            )
            remaining = max(0, budget - (snapshot["time_ms"] - pause["started_ms"]))
        gap = noise["interval"]["open_gap_active_ms"] + max(0, tail_ms - remaining)
        if gap >= 60000:
            findings.append(
                finding(
                    "fail" if gap >= 300000 else "warn",
                    "SENSOR_NOISE_GAP",
                    f"ICS43434: open audio block gap {gap / 1000:g}s active "
                    "including host tail",
                )
            )
        if not state["full_noise_windows"]:
            current = noise["window_current"]
            hz = (
                current["successful_blocks"] * 1000 / current["active_ms"]
                if current["active_ms"]
                else 0
            )
            findings.append(
                finding(
                    "warn",
                    "SENSOR_NOISE_COVERAGE",
                    "ICS43434: no full 300s active window after baseline; "
                    f"preliminary frequency {hz:g} Hz",
                )
            )
    return findings


def check_observation_cadence(
    report: dict, reference_time: datetime | None
) -> SensorCadenceReport:
    findings = []
    latest = {(m["sensor"], m["metric"]): m for m in report.get("latest", [])}
    states = {
        (s["boot_id"], s["sensor"]): s
        for s in report.get("acceptance", {}).get("sensors", [])
    }
    for snapshot in report.get("sensors", []):
        sensor = snapshot["sensor"]
        if sensor == "ICS43434":
            continue
        state = states.get((snapshot["boot_id"], sensor))
        if state is None:
            continue
        settings = (report.get("expected_settings") or {}).get(
            sensor, snapshot["settings"]
        )
        interval = settings["measurement_interval_ms"] / 1000
        warn, fail = interval + 60, 2 * interval + 300
        tail = elapsed(reference_time, state["received_ts"])
        for name, metric in state["metrics"].items():
            item = latest[sensor, name]
            age = item["age_seconds"]
            age = age + tail if age is not None else None
            gap = max(metric["max_gap_seconds"], age or 0)
            first_wait = metric["first_wait_seconds"]
            if first_wait is None:
                first_wait = state["elapsed_seconds"] + tail
            status = (
                "fail"
                if gap >= fail or first_wait >= interval + 300
                else "warn"
                if (
                    gap >= warn
                    or first_wait >= warn
                    or metric["successes"] < 3
                    or metric["unobserved_intervals"]
                )
                else "ok"
            )
            message = (
                f"{sensor}/{name}: {metric['successes']} capture measurements; "
                f"first wait {first_wait:g}s (WARN {warn:g}, FAIL {interval + 300:g}); "
                f"max gap/age {gap:g}s (WARN {warn:g}, FAIL {fail:g})"
            )
            if metric["successes"] < 3:
                message += (
                    "; coverage incomplete: at least three measurements "
                    "required for cadence"
                )
            if metric["unobserved_intervals"]:
                message += (
                    "; coverage incomplete: intermediate measurements not observed"
                )
            findings.append(
                SensorCadenceFinding(
                    status=status,
                    sensor=sensor,
                    metric=name,
                    canonical_metric=canonical_metric_name(name),
                    samples_count=metric["successes"],
                    max_gap_seconds=gap,
                    tail_gap_seconds=age,
                    expected_interval_seconds=interval,
                    warn_after_seconds=warn,
                    fail_after_seconds=fail,
                    message=message,
                )
            )
    warnings = sum(f.status == "warn" for f in findings)
    failures = sum(f.status == "fail" for f in findings)
    return SensorCadenceReport(
        status="fail" if failures else "warn" if warnings else "ok",
        checked_series_count=len(findings),
        warning_count=warnings,
        failure_count=failures,
        findings=tuple(findings),
        message=f"UART v2 cadence: {failures} failures, {warnings} warnings",
    )
