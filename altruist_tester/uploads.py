"""In-memory upload delivery statistics."""

from __future__ import annotations

from dataclasses import dataclass, field

from altruist_tester.parsers.upload_events import UploadChannel, UploadEvent

UPLOAD_CHANNELS: tuple[UploadChannel, ...] = ("connectivity", "datalog")


@dataclass(slots=True)
class UploadChannelStats:
    """Mutable delivery counters for one upload channel."""

    channel: UploadChannel
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    skipped: int = 0
    warnings: int = 0
    max_consecutive_failures: int = 0
    last_status: str | None = None
    last_reason: str | None = None
    targets: set[str] = field(default_factory=set)
    failure_reasons: dict[str, int] = field(default_factory=dict)
    warning_reasons: dict[str, int] = field(default_factory=dict)
    _current_consecutive_failures: int = 0
    _outcomes: int = 0
    _pending_attempt: bool = False

    @property
    def observed(self) -> bool:
        """Return whether any upload line was parsed for this channel."""

        return (
            self.attempts
            + self.successes
            + self.failures
            + self.skipped
            + self.warnings
            > 0
        )

    @property
    def effective_attempts(self) -> int:
        """Return attempts, falling back to outcomes when attempts are not logged."""

        return max(self.attempts, self._outcomes)

    @property
    def success_rate(self) -> float | None:
        """Return successful outcome ratio, if any outcomes were observed."""

        if self._outcomes == 0:
            return None
        return self.successes / self._outcomes

    def append(self, event: UploadEvent) -> None:
        """Add one upload event to the channel counters."""

        self.last_status = event.status
        self.last_reason = event.reason
        if event.target:
            self.targets.add(event.target)

        if event.status == "attempt":
            if self.channel == "datalog" and self._pending_attempt:
                return
            self.attempts += 1
            self._pending_attempt = True
            return
        if event.status == "target":
            return
        if event.status == "success":
            self.successes += 1
            self._outcomes += 1
            self._pending_attempt = False
            self._current_consecutive_failures = 0
            return
        if event.status == "failure":
            self.failures += 1
            self._outcomes += 1
            self._pending_attempt = False
            self._current_consecutive_failures += 1
            self.max_consecutive_failures = max(
                self.max_consecutive_failures,
                self._current_consecutive_failures,
            )
            if event.reason:
                self.failure_reasons[event.reason] = (
                    self.failure_reasons.get(event.reason, 0) + 1
                )
            return
        if event.status == "skipped":
            self.skipped += 1
            if event.reason:
                self.warning_reasons[event.reason] = (
                    self.warning_reasons.get(event.reason, 0) + 1
                )
            return
        if event.status == "warning":
            self.warnings += 1
            if event.reason:
                self.warning_reasons[event.reason] = (
                    self.warning_reasons.get(event.reason, 0) + 1
                )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly channel summary."""

        return {
            "channel": self.channel,
            "observed": self.observed,
            "attempts": self.effective_attempts,
            "logged_attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "success_rate": self.success_rate,
            "max_consecutive_failures": self.max_consecutive_failures,
            "last_status": self.last_status,
            "last_reason": self.last_reason,
            "targets": sorted(self.targets),
            "failure_reasons": dict(sorted(self.failure_reasons.items())),
            "warning_reasons": dict(sorted(self.warning_reasons.items())),
        }


@dataclass(slots=True)
class UploadStats:
    """Upload delivery counters for all supported channels."""

    channels: dict[UploadChannel, UploadChannelStats] = field(
        default_factory=lambda: {
            channel: UploadChannelStats(channel) for channel in UPLOAD_CHANNELS
        }
    )

    def append(self, event: UploadEvent) -> None:
        """Add one parsed upload event."""

        self.channels[event.channel].append(event)

    def channel(self, name: UploadChannel) -> UploadChannelStats:
        """Return stats for one upload channel."""

        return self.channels[name]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly summary for all channels."""

        return {
            channel: self.channels[channel].as_dict() for channel in UPLOAD_CHANNELS
        }
