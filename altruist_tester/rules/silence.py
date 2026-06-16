"""Serial output silence checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SerialSilenceStatus = Literal["ok", "warn", "fail"]

DEFAULT_SILENCE_WARN_AFTER_SECONDS = 2 * 60
DEFAULT_SILENCE_FAIL_AFTER_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class SerialSilenceFinding:
    """One serial silence threshold finding."""

    status: SerialSilenceStatus
    code: str
    silence_seconds: float
    warn_after_seconds: int
    fail_after_seconds: int
    message: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly finding."""

        return {
            "status": self.status,
            "code": self.code,
            "silence_seconds": self.silence_seconds,
            "warn_after_seconds": self.warn_after_seconds,
            "fail_after_seconds": self.fail_after_seconds,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SerialSilenceReport:
    """Aggregate serial silence check result."""

    status: SerialSilenceStatus
    lines_read: int
    first_line_elapsed_seconds: float | None
    last_line_elapsed_seconds: float | None
    max_interline_gap_seconds: float | None
    tail_silence_seconds: float | None
    max_silence_seconds: float
    warning_count: int
    failure_count: int
    findings: tuple[SerialSilenceFinding, ...]
    message: str

    @property
    def ok(self) -> bool:
        """Return True when the serial silence check did not fail."""

        return self.status != "fail"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly report."""

        return {
            "status": self.status,
            "lines_read": self.lines_read,
            "first_line_elapsed_seconds": self.first_line_elapsed_seconds,
            "last_line_elapsed_seconds": self.last_line_elapsed_seconds,
            "max_interline_gap_seconds": self.max_interline_gap_seconds,
            "tail_silence_seconds": self.tail_silence_seconds,
            "max_silence_seconds": self.max_silence_seconds,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "message": self.message,
        }


def _status_for_silence(
    silence_seconds: float,
    *,
    warn_after_seconds: int,
    fail_after_seconds: int,
) -> SerialSilenceStatus | None:
    if silence_seconds >= fail_after_seconds:
        return "fail"
    if silence_seconds >= warn_after_seconds:
        return "warn"
    return None


def _finding(
    *,
    status: SerialSilenceStatus,
    code: str,
    silence_seconds: float,
    warn_after_seconds: int,
    fail_after_seconds: int,
    message: str,
) -> SerialSilenceFinding:
    return SerialSilenceFinding(
        status=status,
        code=code,
        silence_seconds=silence_seconds,
        warn_after_seconds=warn_after_seconds,
        fail_after_seconds=fail_after_seconds,
        message=message,
    )


def _append_silence_finding(
    findings: list[SerialSilenceFinding],
    *,
    code: str,
    label: str,
    silence_seconds: float,
    warn_after_seconds: int,
    fail_after_seconds: int,
) -> None:
    status = _status_for_silence(
        silence_seconds,
        warn_after_seconds=warn_after_seconds,
        fail_after_seconds=fail_after_seconds,
    )
    if status is None:
        return
    findings.append(
        _finding(
            status=status,
            code=code,
            silence_seconds=silence_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
            message=f"{label} reached {silence_seconds:g}s",
        )
    )


def check_serial_silence(
    *,
    lines_read: int,
    duration_seconds: int,
    first_line_elapsed_seconds: float | None,
    last_line_elapsed_seconds: float | None,
    max_interline_gap_seconds: float | None,
    warn_after_seconds: int = DEFAULT_SILENCE_WARN_AFTER_SECONDS,
    fail_after_seconds: int = DEFAULT_SILENCE_FAIL_AFTER_SECONDS,
) -> SerialSilenceReport:
    """Check whether serial output went silent for too long."""

    findings: list[SerialSilenceFinding] = []
    bounded_duration = max(0, duration_seconds)

    if lines_read == 0:
        status = _status_for_silence(
            float(bounded_duration),
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
        )
        if status is not None:
            findings.append(
                _finding(
                    status=status,
                    code="NO_SERIAL_OUTPUT",
                    silence_seconds=float(bounded_duration),
                    warn_after_seconds=warn_after_seconds,
                    fail_after_seconds=fail_after_seconds,
                    message=(
                        "No serial output was received; the device may still be "
                        "in Wi-Fi AP/config portal mode"
                    ),
                )
            )
        tail_silence_seconds = None
        max_silence_seconds = float(bounded_duration)
    else:
        initial_silence_seconds = max(0.0, first_line_elapsed_seconds or 0.0)
        tail_silence_seconds = None
        if last_line_elapsed_seconds is not None:
            tail_silence_seconds = max(
                0.0,
                float(bounded_duration) - last_line_elapsed_seconds,
            )

        _append_silence_finding(
            findings,
            code="INITIAL_SERIAL_SILENCE",
            label="Initial serial silence",
            silence_seconds=initial_silence_seconds,
            warn_after_seconds=warn_after_seconds,
            fail_after_seconds=fail_after_seconds,
        )
        if max_interline_gap_seconds is not None:
            _append_silence_finding(
                findings,
                code="INTERLINE_SERIAL_SILENCE",
                label="Serial silence between lines",
                silence_seconds=max_interline_gap_seconds,
                warn_after_seconds=warn_after_seconds,
                fail_after_seconds=fail_after_seconds,
            )
        if tail_silence_seconds is not None:
            _append_silence_finding(
                findings,
                code="TAIL_SERIAL_SILENCE",
                label="Serial silence at end of run",
                silence_seconds=tail_silence_seconds,
                warn_after_seconds=warn_after_seconds,
                fail_after_seconds=fail_after_seconds,
            )

        max_silence_seconds = max(
            initial_silence_seconds,
            max_interline_gap_seconds or 0.0,
            tail_silence_seconds or 0.0,
        )

    warning_count = sum(1 for finding in findings if finding.status == "warn")
    failure_count = sum(1 for finding in findings if finding.status == "fail")

    if failure_count:
        status: SerialSilenceStatus = "fail"
        message = f"{failure_count} serial silence checks failed"
    elif warning_count:
        status = "warn"
        message = f"{warning_count} serial silence checks produced warnings"
    else:
        status = "ok"
        message = "Serial output stayed active"

    return SerialSilenceReport(
        status=status,
        lines_read=lines_read,
        first_line_elapsed_seconds=first_line_elapsed_seconds,
        last_line_elapsed_seconds=last_line_elapsed_seconds,
        max_interline_gap_seconds=max_interline_gap_seconds,
        tail_silence_seconds=tail_silence_seconds,
        max_silence_seconds=max_silence_seconds,
        warning_count=warning_count,
        failure_count=failure_count,
        findings=tuple(findings),
        message=message,
    )
