"""Parser for development firmware metrics blocks."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_HEADER_RE = re.compile(r"^===\s+\[(?P<model>[^\]]+)\]\s+METRICS\s+===")
_STATUS_RE = re.compile(r"\b(?P<status>ALIVE|ERROR)\b", re.IGNORECASE)
_UPTIME_TOTAL_RE = re.compile(r"\((?P<seconds>\d+)s\s+total\)")
_BOOT_RE = re.compile(r"^Boot:\s*(?P<boot>\d+)")
_WIFI_RE = re.compile(r"^WiFi:\s*.*?\b(?P<state>OK|DISCONNECTED)\b", re.IGNORECASE)
_RSSI_RE = re.compile(r"RSSI:\s*(?P<rssi>-?\d+)\s*dBm", re.IGNORECASE)
_TX_RE = re.compile(r"^TX:\s*(?P<tx>\d+)")
_LAST_TX_RE = re.compile(r"last:\s*(?P<age>\d+)s\s+ago", re.IGNORECASE)
_ERRORS_RE = re.compile(
    r"^Errors:\s*WiFi=(?P<wifi>\d+)\s+Sensor=(?P<sensor>\d+)\s+SD=(?P<sd>\d+)",
    re.IGNORECASE,
)
_ESP_TEMP_RE = re.compile(r"^ESP Temp:\s*(?P<temp>-?\d+(?:\.\d+)?)")
_FOOTER_PREFIX = "=========================="
_HEALTH_RE = re.compile(
    r"^\[HEALTH\]\s+"
    r"uptime=(?P<uptime>\d+)\s+"
    r"boot=(?P<boot>\d+)\s+"
    r"heap=(?P<heap>\d+)\s+"
    r"rssi=(?P<rssi>-?\d+)\s+"
    r"tx=(?P<tx>\d+)\s+"
    r"errors=(?P<errors>\d+)\s*$"
)


@dataclass(frozen=True, slots=True)
class DevMetricsErrors:
    """Error counters from one development metrics block."""

    wifi: int | None = None
    sensor: int | None = None
    sd: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        """Return counters as a JSON-friendly mapping."""

        return {
            "wifi": self.wifi,
            "sensor": self.sensor,
            "sd": self.sd,
        }


@dataclass(frozen=True, slots=True)
class DevMetrics:
    """Parsed development firmware metrics.

    Fields are optional because serial capture can start or stop in the middle
    of a metrics block, and firmware revisions may add or omit individual
    lines.
    """

    model: str | None = None
    status: str | None = None
    uptime_sec: int | None = None
    boot: int | None = None
    wifi_state: str | None = None
    rssi: int | None = None
    tx: int | None = None
    last_tx_age_sec: int | None = None
    errors: DevMetricsErrors = DevMetricsErrors()
    esp_temp_c: float | None = None
    free_heap: int | None = None
    error_count: int | None = None

    def as_event_payload(self) -> dict[str, object]:
        """Return metrics as an event payload."""

        return {
            "model": self.model,
            "status": self.status,
            "uptime_sec": self.uptime_sec,
            "boot": self.boot,
            "wifi_state": self.wifi_state,
            "rssi": self.rssi,
            "tx": self.tx,
            "last_tx_age_sec": self.last_tx_age_sec,
            "errors": self.errors.as_dict(),
            "esp_temp_c": self.esp_temp_c,
            "free_heap": self.free_heap,
            "error_count": self.error_count,
        }


def _parse_health_line(line: str) -> DevMetrics | None:
    match = _HEALTH_RE.match(line.strip())
    if match is None:
        return None

    rssi = int(match.group("rssi"))
    error_count = int(match.group("errors"))
    return DevMetrics(
        status="ERROR" if error_count else "ALIVE",
        uptime_sec=int(match.group("uptime")),
        boot=int(match.group("boot")),
        wifi_state="DISCONNECTED" if rssi == 0 else "OK",
        rssi=rssi,
        tx=int(match.group("tx")),
        free_heap=int(match.group("heap")),
        error_count=error_count,
    )


def parse_dev_metrics_block(lines: Iterable[str]) -> DevMetrics | None:
    """Parse one metrics block from development firmware output.

    :param lines: Lines from one ``=== [MODEL] METRICS ===`` block.
    :returns: Parsed metrics when a block header is present, otherwise
        ``None``.
    """

    normalized_lines = [line.strip() for line in lines if line.strip()]
    if len(normalized_lines) == 1:
        health_metrics = _parse_health_line(normalized_lines[0])
        if health_metrics is not None:
            return health_metrics

    model: str | None = None
    status: str | None = None
    uptime_sec: int | None = None
    boot: int | None = None
    wifi_state: str | None = None
    rssi: int | None = None
    tx: int | None = None
    last_tx_age_sec: int | None = None
    errors = DevMetricsErrors()
    esp_temp_c: float | None = None

    for line in normalized_lines:
        if header_match := _HEADER_RE.match(line):
            model = header_match.group("model")
            continue

        if line.startswith("Status:"):
            if status_match := _STATUS_RE.search(line):
                status = status_match.group("status").upper()
            continue

        if line.startswith("Uptime:"):
            if uptime_match := _UPTIME_TOTAL_RE.search(line):
                uptime_sec = int(uptime_match.group("seconds"))
            continue

        if boot_match := _BOOT_RE.match(line):
            boot = int(boot_match.group("boot"))
            continue

        if line.startswith("WiFi:"):
            if wifi_match := _WIFI_RE.match(line):
                wifi_state = wifi_match.group("state").upper()
            if rssi_match := _RSSI_RE.search(line):
                rssi = int(rssi_match.group("rssi"))
            continue

        if tx_match := _TX_RE.match(line):
            tx = int(tx_match.group("tx"))
            if last_tx_match := _LAST_TX_RE.search(line):
                last_tx_age_sec = int(last_tx_match.group("age"))
            continue

        if errors_match := _ERRORS_RE.match(line):
            errors = DevMetricsErrors(
                wifi=int(errors_match.group("wifi")),
                sensor=int(errors_match.group("sensor")),
                sd=int(errors_match.group("sd")),
            )
            continue

        if temp_match := _ESP_TEMP_RE.match(line):
            esp_temp_c = float(temp_match.group("temp"))

    if model is None:
        return None

    return DevMetrics(
        model=model,
        status=status,
        uptime_sec=uptime_sec,
        boot=boot,
        wifi_state=wifi_state,
        rssi=rssi,
        tx=tx,
        last_tx_age_sec=last_tx_age_sec,
        errors=errors,
        esp_temp_c=esp_temp_c,
    )


def parse_dev_metrics_blocks(lines: Iterable[str]) -> list[DevMetrics]:
    """Parse all complete or trailing metrics blocks from a line stream.

    A new block header closes the previous block, and a trailing block is
    parsed even without the footer so captures do not lose the last snapshot.
    """

    metrics: list[DevMetrics] = []
    current_block: list[str] | None = None

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if health_metrics := _parse_health_line(line):
            metrics.append(health_metrics)
            continue

        if _HEADER_RE.match(line):
            # A new header closes the previous block even if the footer was
            # dropped or the capture started/stopped mid-print.
            if current_block:
                parsed = parse_dev_metrics_block(current_block)
                if parsed is not None:
                    metrics.append(parsed)
            current_block = [line]
            continue

        if current_block is None:
            continue

        current_block.append(line)
        if line.startswith(_FOOTER_PREFIX):
            parsed = parse_dev_metrics_block(current_block)
            if parsed is not None:
                metrics.append(parsed)
            current_block = None

    if current_block:
        parsed = parse_dev_metrics_block(current_block)
        if parsed is not None:
            metrics.append(parsed)

    return metrics


class DevMetricsStreamParser:
    """Incrementally parse metrics blocks from serial lines.

    Use this parser during live serial capture. It returns a ``DevMetrics``
    object only when a block boundary is reached.
    """

    def __init__(self) -> None:
        self._current_block: list[str] | None = None

    def feed(self, line: str) -> DevMetrics | None:
        """Feed one decoded serial line.

        :returns: Parsed metrics when the previous or current block closes,
            otherwise ``None``.
        """

        line = line.rstrip("\r\n")
        if health_metrics := _parse_health_line(line):
            return health_metrics

        if _HEADER_RE.match(line):
            # Treat a fresh header as an implicit boundary so a missing footer
            # does not make us discard the previous metrics snapshot.
            parsed: DevMetrics | None = None
            if self._current_block:
                parsed = parse_dev_metrics_block(self._current_block)
            self._current_block = [line]
            return parsed

        if self._current_block is None:
            return None

        self._current_block.append(line)
        if line.startswith(_FOOTER_PREFIX):
            parsed = parse_dev_metrics_block(self._current_block)
            self._current_block = None
            return parsed
        return None

    def finish(self) -> DevMetrics | None:
        """Return metrics from a trailing block without a footer, if present."""

        if not self._current_block:
            return None

        # Serial capture can end between the header and footer; keep whatever
        # complete fields were already printed in that final block.
        parsed = parse_dev_metrics_block(self._current_block)
        self._current_block = None
        return parsed
