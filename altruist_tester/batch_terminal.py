"""Small ANSI batch display; worker status remains independent of rendering."""

import os
import shutil
import sys
import textwrap
from dataclasses import dataclass


def terminal_text(value: str) -> str:
    """Keep worker diagnostics from injecting terminal control sequences."""
    return "".join(character if character.isprintable() else " " for character in value)


def compact_worker_error(message: str) -> str:
    """Keep the serial cause visible; the full port/message stays in artifacts."""
    if message.startswith("Serial capture failed on "):
        _, separator, cause = message.partition(": ")
        if separator and cause:
            return cause
    return message


@dataclass
class BatchSlotStatus:
    slot: str
    state: str
    fields: dict[str, str]

    def line(self) -> str:
        details = []
        for key, value in self.fields.items():
            if key == "wifi_age":
                continue
            if key == "wifi" and "wifi_age" in self.fields:
                value += f" (health age={self.fields['wifi_age']})"
            details.append(
                value
                if key in {"message", "progress", "wait_limit"}
                else f"{key}={value}"
            )
        return terminal_text(f"{self.slot} {self.state} " + " | ".join(details))


# Keep the same metric order even when neighboring slots are in different phases.
_COLUMNS = {
    "open": "OPEN",
    "capture": "CAPTURE",
    "progress": "%",
    "bytes": "BYTES",
    "first_bytes": "FIRST",
    "quiet": "QUIET",
    "telemetry": "TELEMETRY",
    "health": "HEALTH",
    "samples": "SAMPLES",
    "wifi": "WIFI",
    "wifi_age": "WIFI AGE",
    "status_age": "STATUS AGE",
    "wait": "WAIT",
    "remaining": "REMAINING",
    "exit": "EXIT",
}


class BatchTerminal:
    """Restore the terminal even if monitoring is interrupted or raises."""

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.enabled and "NO_COLOR" not in os.environ
        self.last_lines: list[str] = []

    def __enter__(self):
        if self.enabled:
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
        return self

    def _paint(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color and code else text

    @staticmethod
    def _state_color(row: BatchSlotStatus) -> str:
        if row.state in {"ERROR", "STATUS_ERROR", "FAILED", "INTERRUPTED"}:
            return "1;31"
        if row.state in {"COMPLETED", "CAPTURE"}:
            return "1;32"
        return "33"

    def _table(
        self, slots: list[BatchSlotStatus], width: int
    ) -> tuple[list[str], bool]:
        values = []
        for row in slots:
            fields = {key: terminal_text(value) for key, value in row.fields.items()}
            if "wait_limit" in fields:
                fields["remaining"] = fields["wait_limit"]
            values.append(fields)
        keys = [key for key in _COLUMNS if any(key in fields for fields in values)]
        labels = {"slot": "SLOT", "state": "STATE", **_COLUMNS}
        widths = {
            "slot": max(4, min(32, max((len(row.slot) for row in slots), default=4))),
            "state": max(5, max((len(row.state) for row in slots), default=5)),
        }
        for key in keys:
            widths[key] = max(
                len(labels[key]), max(len(v.get(key, "-")) for v in values)
            )
        # On narrow terminals, repeat SLOT/STATE for each group of metric columns.
        groups: list[list[str]] = [[]]
        used = widths["slot"] + widths["state"] + 2
        for key in keys:
            if groups[-1] and used + widths[key] + 2 > width:
                groups.append([])
                used = widths["slot"] + widths["state"] + 2
            groups[-1].append(key)
            used += widths[key] + 2
        lines = []
        clipped = any(len(row.slot) > widths["slot"] for row in slots)
        for group in groups:
            if lines:
                lines.append("")
            columns = ["slot", "state", *group]
            header = "  ".join(labels[key].ljust(widths[key]) for key in columns)
            clipped |= len(header) > width
            lines.append(self._paint(header[:width], "1;36"))
            for row, fields in zip(slots, values, strict=True):
                data = {"slot": terminal_text(row.slot), "state": row.state, **fields}
                cells = []
                remaining = width
                for key in columns:
                    value = data.get(key, "-")
                    cell = value[: widths[key]].ljust(widths[key])[:remaining]
                    code = ""
                    if key == "state":
                        code = self._state_color(row)
                    elif key == "slot":
                        code = "1"
                    elif key in {"wifi", "telemetry", "first_bytes"}:
                        code = "32" if value in {"OK", "seen", "yes"} else "33"
                        if value == "DISCONNECTED":
                            code = "31"
                    cells.append(self._paint(cell, code))
                    remaining -= len(cell)
                    if remaining <= 2:
                        break
                    cells.append("  ")
                    remaining -= 2
                lines.append("".join(cells))
        for row in slots:
            message = row.fields.get("message")
            if message:
                detail = terminal_text(f"{row.slot}: {message}")
                lines.extend(
                    self._paint(line, self._state_color(row))
                    for line in textwrap.wrap(detail, width)
                )
        return lines, clipped

    def draw(self, header: str, slots: list[BatchSlotStatus]) -> None:
        self.last_lines = [terminal_text(header), *(row.line() for row in slots)]
        width, height = shutil.get_terminal_size(fallback=(100, 24))
        width = max(1, width - 1)  # Avoid wrapping at the terminal's right edge.
        height = max(1, height)
        header_lines = textwrap.wrap(self.last_lines[0], width)
        lines = [self._paint(line, "1") for line in header_lines]
        table, clipped = self._table(slots, width)
        lines.extend(table)
        clipped |= len(lines) > height - 1
        lines = lines[: height - 1]
        footer = (
            "Clipped: enlarge terminal; details in worker logs/reports."
            if clipped
            else "Ctrl+C stops batch. Status snapshots are not acceptance evidence."
        )
        lines.append(self._paint(footer[:width], "33" if clipped else "2"))
        # Shorter rows must erase old cells to their right, not just rows below.
        sys.stdout.write("\x1b[H" + "\x1b[K\r\n".join(lines) + "\x1b[J")
        sys.stdout.flush()

    def __exit__(self, exc_type, exc_value, traceback):
        if self.enabled:
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
