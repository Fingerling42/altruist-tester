"""Safe, explicit predicates for the bounded firmware diagnostic procedures.

Never return raw lines, HTTP bodies, arbitrary JSON keys or exception messages.
These checks supplement, rather than replace, normal tester acceptance rules.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser

from altruist_tester.parsers.build_events import parse_build_event
from altruist_tester.parsers.dev_metrics import parse_health_line
from altruist_tester.parsers.sensor_values import parse_sensor_values

MAX_BODY = 65536
SERIES = {
    "BME680": frozenset(
        {
            "temperature",
            "humidity",
            "pressure",
            "voc_ppb",
            "voc_index",
            "voc_level",
            "voc_spikes_today",
            "gas_resistance",
            "gas_baseline",
        }
    ),
    "SCD4x": frozenset({"co2", "temperature", "humidity"}),
    "BME280": frozenset({"temperature", "humidity", "pressure"}),
    "ICS43434": frozenset({"noiseAvg", "noiseMax"}),
    "SDS": frozenset({"P1", "P2"}),
}
VOC = frozenset(
    {
        "voc_ppb",
        "voc_index",
        "voc_level",
        "voc_spikes_today",
        "gas_resistance",
        "gas_baseline",
    }
)
TAGS = (
    "BUILD",
    "BOOT",
    "LOG",
    "HEALTH",
    "PAYLOAD",
    "DATALOG",
    "CONNECTIVITY",
    "PROTO",
    "SUBSYSTEM",
)


def result(status: str, reason: str) -> dict:
    return {"status": status, "reason": reason}


def device_id(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F:.-]{12,17}", value):
        return None
    value = re.sub(r"[:.-]", "", value).upper()
    return value if len(value) == 12 else None


def commit_id(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        return value.lower()
    return None


def safe_fields(text: str) -> dict:
    """Extract an allowlist, including from a config line, without echoing it."""
    fields = {}
    if len(text) > MAX_BODY:
        return fields
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict):
        identity = device_id(data.get("sensor_id"))
        if identity:
            fields["device_id"] = identity
        version = data.get("software_version")
        if isinstance(version, str):
            match = re.fullmatch(
                r"R-(URB|INS)_[0-9A-Za-z._-]+(?:\+([0-9a-fA-F]{7,40}))?", version
            )
            if match:
                fields["model"] = "urban" if match[1] == "URB" else "insight"
                if match[2]:
                    fields["commit"] = match[2].lower()
        candidate = data.get("ip")
        if isinstance(candidate, str):
            try:
                fields["ip"] = str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    build = parse_build_event(text)
    if build:
        for key, choices in {
            "model": {"urban", "insight"},
            "profile": {"debug", "release"},
            "channel": {"stable", "testing"},
            "target": {"esp32c3", "esp32c6"},
            "language": {"en", "ru"},
        }.items():
            value = getattr(build, key)
            if value in choices:
                fields[key] = value
        commit = commit_id(build.commit)
        if commit:
            fields["commit"] = commit
    try:
        health = parse_health_line(text)
    except (ValueError, OverflowError, RecursionError):
        health = None
    if health:
        fields.update(boot=health.boot, uptime=health.uptime_sec)
    status = re.match(
        r"^\[LOG\] status uptime=(\d{1,20}) boot=(\d{1,20})(?:\s|$)", text
    )
    if status:
        fields.update(uptime=int(status[1]), boot=int(status[2]))
    ip = re.fullmatch(
        r"(?:\[\d+\] \[INFO\] WiFi connected, IP is: : |"
        r"\[WiFi\] status=\d+ ip=)([0-9a-fA-F:.]+)\s*",
        text,
    )
    if ip:
        try:
            fields["ip"] = str(ipaddress.ip_address(ip[1]))
        except ValueError:
            pass
    identity = re.fullmatch(r"(?:ChipId|sensor_id)[:= ]+([0-9A-Fa-f:.-]+)\s*", text)
    if identity and device_id(identity[1]):
        fields["device_id"] = device_id(identity[1])
    return fields


class LogPage(HTMLParser):
    """Keep typed control values and safe status fields, never page text."""

    def __init__(self):
        super().__init__()
        self.level = None
        self.controls = {}
        self.fields = {}
        self._in_log = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "pre" and attrs.get("id") == "slog":
            self._in_log = True
        if tag == "input" and attrs.get("name") == "debug":
            if attrs.get("value") in {"0", "1", "2", "3", "4", "5"}:
                self.level = int(attrs["value"])
        if tag == "a":
            match = re.fullmatch(
                r"/(?:debug|advanced)\?lvl=([0-5])", attrs.get("href", "")
            )
            if match and attrs.get("aria-pressed") in {"true", "false"}:
                self.controls[int(match[1])] = attrs["aria-pressed"] == "true"

    def handle_endtag(self, tag):
        if tag == "pre":
            self._in_log = False

    def handle_data(self, data):
        if self._in_log:
            for line in data.splitlines():
                self.fields.update(safe_fields(line))

    @property
    def active(self):
        active = [key for key, pressed in self.controls.items() if pressed]
        return active[0] if len(active) == 1 else None


@dataclass(frozen=True)
class State:
    identity: str | None = None
    commit: str | None = None
    model: str | None = None
    profile: str | None = None
    boot: int | None = None
    uptime: int | None = None
    level: int | None = None


def reboot_check(before: State, after: State) -> dict:
    def number(value):
        return value if type(value) is int and 0 <= value < 2**64 else None

    witness = {
        "before_boot": number(before.boot),
        "after_boot": number(after.boot),
        "before_uptime": number(before.uptime),
        "after_uptime": number(after.uptime),
    }

    def recorded(status, reason):
        return {**result(status, reason), "witness": witness}

    if not all((before.identity, after.identity, before.commit, after.commit)):
        return recorded("NOT_CHECKED", "identity_witness_unavailable")
    if before.identity != after.identity or before.commit != after.commit:
        return recorded("FAIL", "identity_or_version_changed")
    if witness["before_boot"] is not None and witness["after_boot"] is not None:
        if after.boot > before.boot:
            return recorded("PASS", "boot_counter_increased")
        return recorded("FAIL", "boot_counter_not_increased")
    if witness["before_uptime"] is not None and witness["after_uptime"] is not None:
        # Exclude the 32-bit millis wrap boundary used by the firmware status.
        if before.uptime >= 4294900:
            return recorded("NOT_CHECKED", "uptime_near_wrap")
        if after.uptime + 1 < before.uptime:
            return recorded("PASS", "uptime_restarted")
        return recorded("FAIL", "uptime_not_restarted")
    return recorded("NOT_CHECKED", "no_boot_witness")


def persistence_check(reboot: dict, after: State, expected: int) -> dict:
    def recorded(status, reason):
        observed = (
            after.level if type(after.level) is int and 0 <= after.level <= 5 else None
        )
        return {
            **result(status, reason),
            "expected_level": expected,
            "observed_level": observed,
        }

    if reboot["status"] != "PASS":
        return recorded("NOT_CHECKED", "new_boot_not_verified")
    if after.level is None:
        return recorded("NOT_CHECKED", "saved_level_unavailable")
    return (
        recorded("PASS", "saved_level_verified")
        if after.level == expected
        else recorded("FAIL", "saved_level_mismatch")
    )


def operational_line(line):
    if line.startswith("[BUILD]"):
        event = parse_build_event(line)
        allowed = {
            "version",
            "channel",
            "commit",
            "model",
            "target",
            "language",
            "profile",
        }
        return bool(event and event.raw_fields and set(event.raw_fields) <= allowed)
    event = (
        r"\[LOG\] uptime=\d+ level=(?:error|event) "
        r"subsystem=[a-z0-9_-]+ event=[a-z0-9_-]+ code=-?\d+"
    )
    status = (
        r"\[LOG\] status uptime=\d+ boot=\d+ reset_reason=[a-z0-9_]+ "
        r"heap=\d+ wifi=[01] wifi_errors=\d+ sensor_errors=\d+ sd_errors=\d+"
    )
    return bool(re.fullmatch(event, line) or re.fullmatch(status, line))


class UartSummary:
    def __init__(self):
        self.pending = b""
        self.fields = {}
        self.conflicts = set()
        self.tags = Counter()
        self.series = Counter()
        self.bytes = 0
        self.unexpected_lines = 0
        self.non_error_logs = 0
        self.oversized_lines = 0
        self._discard = False

    def feed(self, chunk: bytes):
        self.bytes += len(chunk)
        for part in chunk.splitlines(keepends=True):
            if not self._discard:
                self.pending += part
                if len(self.pending) > MAX_BODY:
                    self.pending = b""
                    self._discard = True
                    self.oversized_lines += 1
            if part.endswith(b"\n"):
                if not self._discard:
                    self._line(self.pending.decode("utf-8", errors="replace").rstrip())
                self.pending = b""
                self._discard = False

    def finish(self):
        if self.pending and not self._discard:
            self._line(self.pending.decode("utf-8", errors="replace").rstrip())
            self.pending = b""
        return {
            "bytes": self.bytes,
            "fields": dict(self.fields),
            "identity_conflicts": sorted(self.conflicts),
            "tags": {key: self.tags[key] for key in TAGS},
            "series": [
                {"sensor": sensor, "metric": metric, "count": count}
                for (sensor, metric), count in sorted(self.series.items())
            ],
            "sensor_json_records": sum(self.series.values()),
            "unexpected_lines": self.unexpected_lines,
            "non_error_logs": self.non_error_logs,
            "oversized_lines": self.oversized_lines,
        }

    def _line(self, line):
        fields = safe_fields(line)
        for key in ("device_id", "model", "profile", "commit"):
            if key in self.fields and key in fields and self.fields[key] != fields[key]:
                self.conflicts.add(key)
        self.fields.update(fields)
        tag = re.match(r"^\[([A-Z]+)\]", line)
        if tag and tag[1] in TAGS:
            self.tags[tag[1]] += 1
        if line and not operational_line(line):
            self.unexpected_lines += 1
        if line.startswith("[LOG] uptime=") and " level=error " not in line:
            self.non_error_logs += 1
        for sample in parse_sensor_values(line):
            if sample.source == "serial" and sample.metric in SERIES.get(
                sample.sensor, ()
            ):
                self.series[sample.key] += 1


def uart_check(summary: dict, profile: str, model: str, mode: str) -> dict:
    if summary["oversized_lines"]:
        return result("NOT_CHECKED", "oversized_uart_line")
    if profile == "release":
        if mode == "off":
            return (
                result("PASS", "quiet_window")
                if summary["bytes"] == 0
                else result("FAIL", "off_not_quiet")
            )
        forbidden = (
            "BOOT",
            "HEALTH",
            "PAYLOAD",
            "DATALOG",
            "CONNECTIVITY",
            "PROTO",
            "SUBSYSTEM",
        )
        if (
            summary["unexpected_lines"]
            or summary["sensor_json_records"]
            or any(summary["tags"][tag] for tag in forbidden)
        ):
            return result("FAIL", "release_detailed_output")
        if mode == "errors" and summary["non_error_logs"]:
            return result("FAIL", "non_error_in_errors_mode")
        if not summary["tags"]["LOG"]:
            return result("NOT_CHECKED", "no_operational_event_observed")
        return result("PASS", "operational_window")
    if not summary["tags"]["HEALTH"] or not summary["sensor_json_records"]:
        return result("FAIL", "health_or_sensor_json_missing")
    observed = {
        item["metric"] for item in summary["series"] if item["sensor"] == "BME680"
    }
    if model == "insight" and not VOC <= observed:
        return result("FAIL", "voc_gas_missing")
    return result("PASS", "debug_measurements_observed")
