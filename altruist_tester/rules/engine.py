"""Central rule evaluation for completed serial captures."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from altruist_tester.rules.cadence import (
    SensorCadenceFinding,
    SensorCadenceReport,
    check_sensor_cadence,
)
from altruist_tester.rules.defaults import (
    DEFAULT_NON_NEGATIVE_METRICS,
    DEFAULT_SENSOR_RANGES,
    SensorRange,
    SensorRangeReport,
    check_sensor_sample_ranges,
)
from altruist_tester.rules.flatline import (
    SensorFlatlineFinding,
    SensorFlatlineReport,
    check_sensor_flatlines,
)
from altruist_tester.rules.log_contract import LogContractReport, check_log_contract
from altruist_tester.rules.presence import SensorPresenceReport, check_sensor_presence
from altruist_tester.rules.runtime import RuntimeCounterReport, check_runtime_counters
from altruist_tester.rules.silence import SerialSilenceReport, check_serial_silence
from altruist_tester.rules.subsystems import (
    SubsystemHealthReport,
    check_subsystem_health,
)
from altruist_tester.rules.uploads import (
    UploadChannelConfig,
    UploadHealthReport,
    check_upload_health,
)
from altruist_tester.serial_logger import SerialLogStats

RuleStatus = Literal["ok", "warn", "fail"]
RuleSeverity = Literal["warn", "fail"]
RuleVerdict = Literal["PASS_CANDIDATE", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class RuleEngineConfig:
    """Rule inputs that are not captured directly from the serial stream.

    The CLI builds this object from command-line options, tester config, and
    run timing information before evaluating the final health verdict.
    """

    expected_metrics: tuple[str, ...] = ()
    expected_sensors: tuple[str, ...] = ()
    sensor_ranges: Mapping[str, SensorRange] = field(
        default_factory=lambda: dict(DEFAULT_SENSOR_RANGES)
    )
    warn_on_unknown_ranges: bool = False
    unknown_non_negative_metrics: Collection[str] = DEFAULT_NON_NEGATIVE_METRICS
    flatline_window_seconds: int = 30 * 60
    flatline_fail_after_seconds: int = 60 * 60
    flatline_min_distinct_values: int = 2
    cadence_expected_interval_seconds: int = 5 * 60
    cadence_warn_after_missed: int = 2
    cadence_fail_after_missed: int = 4
    silence_warn_after_seconds: int = 2 * 60
    silence_fail_after_seconds: int = 10 * 60
    connectivity_upload: UploadChannelConfig = field(
        default_factory=UploadChannelConfig
    )
    datalog_upload: UploadChannelConfig = field(default_factory=UploadChannelConfig)
    log_contract_startup_window_seconds: int = 10 * 60
    reference_time: datetime | None = None
    max_tail_window_seconds: int | None = None
    duration_seconds: int = 0


@dataclass(frozen=True, slots=True)
class RuleFinding:
    """Normalized finding returned by the rules engine."""

    severity: RuleSeverity
    code: str
    message: str
    rule: str
    first_seen: str | None = None
    last_seen: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "rule": self.rule,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True, slots=True)
class RuleEngineReports:
    """Raw per-rule reports produced by one rule evaluation."""

    sensor_presence: SensorPresenceReport
    sensor_ranges: SensorRangeReport
    sensor_flatlines: SensorFlatlineReport
    sensor_cadence: SensorCadenceReport
    runtime_counters: RuntimeCounterReport
    serial_silence: SerialSilenceReport
    log_contract: LogContractReport
    subsystem_health: SubsystemHealthReport
    upload_health: UploadHealthReport

    def as_dict(self) -> dict[str, object]:
        """Return JSON-friendly per-rule report payloads."""

        return {
            "sensor_presence": self.sensor_presence.as_dict(),
            "sensor_ranges": self.sensor_ranges.as_dict(),
            "sensor_flatlines": self.sensor_flatlines.as_dict(),
            "sensor_cadence": self.sensor_cadence.as_dict(),
            "runtime_counters": self.runtime_counters.as_dict(),
            "serial_silence": self.serial_silence.as_dict(),
            "log_contract": self.log_contract.as_dict(),
            "subsystem_health": self.subsystem_health.as_dict(),
            "upload_health": self.upload_health.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class RuleEngineResult:
    """Centralized verdict and findings for a completed capture.

    ``reports`` keeps the raw per-rule payloads. ``findings`` is a normalized
    flattened view used by summaries, text reports, and process exit decisions.
    """

    verdict: RuleVerdict
    status: RuleStatus
    findings: tuple[RuleFinding, ...]
    reports: RuleEngineReports

    @property
    def ok(self) -> bool:
        """Return True when the centralized verdict did not fail."""

        return self.verdict != "FAIL"

    @property
    def failed_checks(self) -> tuple[str, ...]:
        """Return names of rules that produced at least one fail finding."""

        return tuple(
            dict.fromkeys(
                finding.rule for finding in self.findings if finding.severity == "fail"
            )
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly result."""

        return {
            "verdict": self.verdict,
            "status": self.status,
            "findings": [finding.as_dict() for finding in self.findings],
            "failed_checks": list(self.failed_checks),
        }


def _verdict_from_findings(findings: tuple[RuleFinding, ...]) -> RuleVerdict:
    if any(finding.severity == "fail" for finding in findings):
        return "FAIL"
    if any(finding.severity == "warn" for finding in findings):
        return "WARN"
    return "PASS_CANDIDATE"


def _status_from_verdict(verdict: RuleVerdict) -> RuleStatus:
    if verdict == "FAIL":
        return "fail"
    if verdict == "WARN":
        return "warn"
    return "ok"


def _report_finding(
    *,
    rule: str,
    status: str,
    code: str,
    message: str,
) -> RuleFinding | None:
    if status not in {"warn", "fail"}:
        return None
    return RuleFinding(
        severity=status,
        code=code,
        message=message,
        rule=rule,
    )


def _status_code(rule: str, status: str) -> str:
    return f"{rule.upper()}_{status.upper()}"


def _report_status_finding(rule: str, status: str, message: str) -> RuleFinding | None:
    return _report_finding(
        rule=rule,
        status=status,
        code=_status_code(rule, status),
        message=message,
    )


def _series_code(prefix: str, sensor: str, metric: str, status: str) -> str:
    code_parts = (prefix, sensor, metric, status)
    return "_".join(
        "".join(char if char.isalnum() else "_" for char in part.upper())
        for part in code_parts
    )


def _collect_sensor_range_findings(
    report: SensorRangeReport,
) -> tuple[RuleFinding, ...]:
    findings = []
    for check in report.findings:
        finding = _report_finding(
            rule="sensor_ranges",
            status=check.status,
            code=_series_code("SENSOR_RANGE", "sample", check.metric, check.status),
            message=check.message,
        )
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def _collect_series_findings(
    *,
    rule: str,
    prefix: str,
    raw_findings: tuple[SensorFlatlineFinding, ...] | tuple[SensorCadenceFinding, ...],
) -> tuple[RuleFinding, ...]:
    findings = []
    for raw_finding in raw_findings:
        status = raw_finding.status
        finding = _report_finding(
            rule=rule,
            status=status,
            code=_series_code(
                prefix,
                raw_finding.sensor,
                raw_finding.metric,
                status,
            ),
            message=raw_finding.message,
        )
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def _collect_runtime_findings(report: RuntimeCounterReport) -> tuple[RuleFinding, ...]:
    if report.findings:
        return tuple(
            RuleFinding(
                severity=finding.status,
                code=finding.code,
                message=finding.message,
                rule="runtime_counters",
            )
            for finding in report.findings
        )

    finding = _report_status_finding(
        "runtime_counters",
        report.status,
        report.message,
    )
    return () if finding is None else (finding,)


def _collect_serial_silence_findings(
    report: SerialSilenceReport,
) -> tuple[RuleFinding, ...]:
    if report.findings:
        return tuple(
            RuleFinding(
                severity=finding.status,
                code=finding.code,
                message=finding.message,
                rule="serial_silence",
            )
            for finding in report.findings
        )

    finding = _report_status_finding("serial_silence", report.status, report.message)
    return () if finding is None else (finding,)


def _collect_log_contract_findings(
    report: LogContractReport,
) -> tuple[RuleFinding, ...]:
    return tuple(
        RuleFinding(
            severity=finding.status,
            code=finding.code,
            message=finding.message,
            rule="log_contract",
        )
        for finding in report.findings
    )


def _collect_upload_findings(report: UploadHealthReport) -> tuple[RuleFinding, ...]:
    if report.findings:
        return tuple(
            RuleFinding(
                severity=finding.status,
                code=finding.code,
                message=finding.message,
                rule="upload_health",
            )
            for finding in report.findings
        )

    finding = _report_status_finding("upload_health", report.status, report.message)
    return () if finding is None else (finding,)


def _subsystem_code(subsystem: str, reason: str, status: str) -> str:
    code_parts = ("SUBSYSTEM", subsystem, reason, status)
    return "_".join(
        "".join(char if char.isalnum() else "_" for char in part.upper())
        for part in code_parts
    )


def _collect_subsystem_findings(
    report: SubsystemHealthReport,
) -> tuple[RuleFinding, ...]:
    return tuple(
        RuleFinding(
            severity=finding.status,
            code=_subsystem_code(finding.subsystem, finding.reason, finding.status),
            message=finding.message,
            rule="subsystem_health",
            first_seen=finding.first_seen,
            last_seen=finding.last_seen,
        )
        for finding in report.findings
        if finding.status in {"warn", "fail"}
    )


def evaluate_rules(
    stats: SerialLogStats,
    config: RuleEngineConfig,
) -> RuleEngineResult:
    """Run all health rules and return a centralized verdict.

    The engine evaluates presence, ranges, flatlines, cadence, runtime
    counters, and serial silence, then collapses their warnings/failures into
    one process-level verdict.
    """

    sensor_presence = check_sensor_presence(
        stats.sensor_series,
        expected_metrics=config.expected_metrics,
        expected_sensors=config.expected_sensors,
    )
    sensor_ranges = check_sensor_sample_ranges(
        (
            sample
            for records in stats.sensor_series.by_key.values()
            for sample in records
        ),
        ranges=config.sensor_ranges,
        warn_on_unknown=config.warn_on_unknown_ranges,
        unknown_non_negative_metrics=config.unknown_non_negative_metrics,
    )
    sensor_flatlines = check_sensor_flatlines(
        stats.sensor_series,
        flatline_window_seconds=config.flatline_window_seconds,
        flatline_fail_after_seconds=config.flatline_fail_after_seconds,
        min_distinct_values=config.flatline_min_distinct_values,
    )
    sensor_cadence = check_sensor_cadence(
        stats.sensor_series,
        reference_time=config.reference_time,
        max_tail_window_seconds=config.max_tail_window_seconds,
        expected_interval_seconds=config.cadence_expected_interval_seconds,
        warn_after_missed=config.cadence_warn_after_missed,
        fail_after_missed=config.cadence_fail_after_missed,
    )
    runtime_counters = check_runtime_counters(stats.dev_metrics_records)
    serial_silence = check_serial_silence(
        lines_read=stats.lines_read,
        duration_seconds=config.duration_seconds,
        first_line_elapsed_seconds=stats.first_line_elapsed_seconds,
        last_line_elapsed_seconds=stats.last_line_elapsed_seconds,
        max_interline_gap_seconds=stats.max_interline_gap_seconds,
        warn_after_seconds=config.silence_warn_after_seconds,
        fail_after_seconds=config.silence_fail_after_seconds,
    )
    log_contract = check_log_contract(
        stats,
        startup_window_seconds=config.log_contract_startup_window_seconds,
        connectivity_upload=config.connectivity_upload,
        datalog_upload=config.datalog_upload,
    )
    subsystem_health = check_subsystem_health(stats.subsystem_event_records)
    upload_health = check_upload_health(
        stats.upload_stats,
        connectivity=config.connectivity_upload,
        datalog=config.datalog_upload,
    )
    reports = RuleEngineReports(
        sensor_presence=sensor_presence,
        sensor_ranges=sensor_ranges,
        sensor_flatlines=sensor_flatlines,
        sensor_cadence=sensor_cadence,
        runtime_counters=runtime_counters,
        serial_silence=serial_silence,
        log_contract=log_contract,
        subsystem_health=subsystem_health,
        upload_health=upload_health,
    )

    findings = tuple(
        finding
        for finding in (
            _report_status_finding(
                "sensor_presence",
                sensor_presence.status,
                sensor_presence.message,
            ),
            *_collect_sensor_range_findings(sensor_ranges),
            *_collect_series_findings(
                rule="sensor_flatlines",
                prefix="SENSOR_FLATLINE",
                raw_findings=sensor_flatlines.findings,
            ),
            *_collect_series_findings(
                rule="sensor_cadence",
                prefix="SENSOR_CADENCE",
                raw_findings=sensor_cadence.findings,
            ),
            *_collect_runtime_findings(runtime_counters),
            *_collect_serial_silence_findings(serial_silence),
            *_collect_log_contract_findings(log_contract),
            *_collect_subsystem_findings(subsystem_health),
            *_collect_upload_findings(upload_health),
        )
        if finding is not None
    )
    verdict = _verdict_from_findings(findings)
    return RuleEngineResult(
        verdict=verdict,
        status=_status_from_verdict(verdict),
        findings=findings,
        reports=reports,
    )
