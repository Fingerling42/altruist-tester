"""Upload delivery health checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from altruist_tester.parsers.upload_events import UploadChannel
from altruist_tester.rules.severity import severity_for_upload_mode
from altruist_tester.uploads import UPLOAD_CHANNELS, UploadChannelStats, UploadStats

UploadMode = Literal["disabled", "optional", "required"]
UploadStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class UploadChannelConfig:
    """Rule configuration for one upload channel."""

    mode: UploadMode = "disabled"
    min_successes: int = 1
    min_success_rate: float = 0.8
    max_consecutive_failures: int | None = None


DEFAULT_UPLOAD_CHANNEL_CONFIG = UploadChannelConfig()


@dataclass(frozen=True, slots=True)
class UploadFinding:
    """One upload delivery finding."""

    status: UploadStatus
    channel: UploadChannel
    code: str
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "channel": self.channel,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class UploadChannelReport:
    """Upload health report for one channel."""

    status: UploadStatus
    channel: UploadChannel
    mode: UploadMode
    attempts: int
    successes: int
    failures: int
    skipped: int
    warnings: int
    success_rate: float | None
    max_consecutive_failures: int
    targets: tuple[str, ...]
    failure_reasons: dict[str, int]
    warning_reasons: dict[str, int]
    findings: tuple[UploadFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the channel did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly channel report."""

        return {
            "status": self.status,
            "channel": self.channel,
            "mode": self.mode,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "success_rate": self.success_rate,
            "max_consecutive_failures": self.max_consecutive_failures,
            "targets": list(self.targets),
            "failure_reasons": self.failure_reasons,
            "warning_reasons": self.warning_reasons,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class UploadHealthReport:
    """Aggregate upload delivery health report."""

    status: UploadStatus
    channels: dict[str, UploadChannelReport]
    warning_count: int
    failure_count: int
    findings: tuple[UploadFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when upload checks did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly aggregate report."""

        return {
            "status": self.status,
            "channels": {
                channel: report.as_dict()
                for channel, report in sorted(self.channels.items())
            },
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _finding_status(mode: UploadMode) -> UploadStatus:
    return severity_for_upload_mode(mode)


def _finding(
    *,
    mode: UploadMode,
    channel: UploadChannel,
    code: str,
    message: str,
) -> UploadFinding:
    return UploadFinding(
        status=_finding_status(mode),
        channel=channel,
        code=code,
        message=message,
    )


def _check_channel(
    stats: UploadChannelStats,
    config: UploadChannelConfig,
) -> UploadChannelReport:
    if config.mode == "disabled":
        return UploadChannelReport(
            status="ok",
            channel=stats.channel,
            mode=config.mode,
            attempts=stats.effective_attempts,
            successes=stats.successes,
            failures=stats.failures,
            skipped=stats.skipped,
            warnings=stats.warnings,
            success_rate=stats.success_rate,
            max_consecutive_failures=stats.max_consecutive_failures,
            targets=tuple(sorted(stats.targets)),
            failure_reasons=dict(sorted(stats.failure_reasons.items())),
            warning_reasons=dict(sorted(stats.warning_reasons.items())),
            findings=(),
            message=f"{stats.channel} upload check is disabled",
        )

    findings: list[UploadFinding] = []
    if not stats.observed:
        findings.append(
            _finding(
                mode=config.mode,
                channel=stats.channel,
                code="UPLOAD_NOT_OBSERVED",
                message=f"{stats.channel} upload activity was not observed",
            )
        )
    if stats.successes < config.min_successes:
        findings.append(
            _finding(
                mode=config.mode,
                channel=stats.channel,
                code="UPLOAD_TOO_FEW_SUCCESSES",
                message=(
                    f"{stats.channel} upload successes {stats.successes} "
                    f"below required {config.min_successes}"
                ),
            )
        )
    if stats.success_rate is not None and stats.success_rate < config.min_success_rate:
        findings.append(
            _finding(
                mode=config.mode,
                channel=stats.channel,
                code="UPLOAD_SUCCESS_RATE_LOW",
                message=(
                    f"{stats.channel} upload success rate "
                    f"{stats.success_rate:.3f} below {config.min_success_rate:.3f}"
                ),
            )
        )
    if (
        config.max_consecutive_failures is not None
        and stats.max_consecutive_failures > config.max_consecutive_failures
    ):
        findings.append(
            _finding(
                mode=config.mode,
                channel=stats.channel,
                code="UPLOAD_CONSECUTIVE_FAILURES_HIGH",
                message=(
                    f"{stats.channel} upload had {stats.max_consecutive_failures} "
                    "consecutive failures; limit is "
                    f"{config.max_consecutive_failures}"
                ),
            )
        )

    if any(finding.status == "fail" for finding in findings):
        status: UploadStatus = "fail"
    elif findings:
        status = "warn"
    else:
        status = "ok"

    if status == "ok":
        message = f"{stats.channel} upload delivery satisfied configured thresholds"
    else:
        message = f"{stats.channel} upload delivery needs attention"

    return UploadChannelReport(
        status=status,
        channel=stats.channel,
        mode=config.mode,
        attempts=stats.effective_attempts,
        successes=stats.successes,
        failures=stats.failures,
        skipped=stats.skipped,
        warnings=stats.warnings,
        success_rate=stats.success_rate,
        max_consecutive_failures=stats.max_consecutive_failures,
        targets=tuple(sorted(stats.targets)),
        failure_reasons=dict(sorted(stats.failure_reasons.items())),
        warning_reasons=dict(sorted(stats.warning_reasons.items())),
        findings=tuple(findings),
        message=message,
    )


def check_upload_health(
    stats: UploadStats,
    *,
    connectivity: UploadChannelConfig = DEFAULT_UPLOAD_CHANNEL_CONFIG,
    datalog: UploadChannelConfig = DEFAULT_UPLOAD_CHANNEL_CONFIG,
) -> UploadHealthReport:
    """Check upload delivery according to configured channel modes."""

    configs = {
        "connectivity": connectivity,
        "datalog": datalog,
    }
    channel_reports = {
        channel: _check_channel(stats.channel(channel), configs[channel])
        for channel in UPLOAD_CHANNELS
    }
    findings = tuple(
        finding for report in channel_reports.values() for finding in report.findings
    )
    warning_count = sum(1 for finding in findings if finding.status == "warn")
    failure_count = sum(1 for finding in findings if finding.status == "fail")

    if failure_count:
        status: UploadStatus = "fail"
        message = f"{failure_count} upload checks failed"
    elif warning_count:
        status = "warn"
        message = f"{warning_count} upload checks produced warnings"
    else:
        status = "ok"
        message = "Upload checks passed or were disabled"

    return UploadHealthReport(
        status=status,
        channels=channel_reports,
        warning_count=warning_count,
        failure_count=failure_count,
        findings=findings,
        message=message,
    )
