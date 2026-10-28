"""Small ANSI batch display; worker status remains independent of rendering."""

import os
import shutil
import sys
import textwrap


def terminal_text(value: str) -> str:
    """Keep worker diagnostics from injecting terminal control sequences."""
    return "".join(character if character.isprintable() else " " for character in value)


class BatchTerminal:
    """Restore the terminal even if monitoring is interrupted or raises."""

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        self.last_lines: list[str] = []

    def __enter__(self):
        if self.enabled:
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
        return self

    def draw(self, header: str, slots: list[str]) -> None:
        self.last_lines = [terminal_text(line) for line in [header, *slots]]
        width, height = shutil.get_terminal_size(fallback=(100, 24))
        width = max(1, width - 1)  # Avoid wrapping at the terminal's right edge.
        height = max(1, height)
        full_header = textwrap.wrap(self.last_lines[0], width)
        header_lines = full_header[: min(2, height - 1)]
        available = max(0, height - len(header_lines) - 1)
        rows_per_slot = max(1, available // max(1, len(slots)))
        lines = list(header_lines)
        clipped = len(full_header) > len(header_lines)
        for slot in self.last_lines[1:]:
            block = textwrap.wrap(slot, width) or [""]
            if len(lines) + rows_per_slot > height - 1:
                clipped = True
                break
            if len(block) > rows_per_slot:
                clipped = True
                block = block[:rows_per_slot]
                block[-1] = block[-1][: max(0, width - 3)] + "." * min(3, width)
            lines.extend(block + [""] * (rows_per_slot - len(block)))
        footer = (
            "Clipped: enlarge terminal; full status printed on exit."
            if clipped
            else "Ctrl+C stops batch. Status snapshots are not acceptance evidence."
        )
        lines.append(footer[:width])
        sys.stdout.write("\x1b[H" + "\r\n".join(lines) + "\x1b[J")
        sys.stdout.flush()

    def __exit__(self, exc_type, exc_value, traceback):
        if self.enabled:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            # Leave the final status in normal terminal scrollback as well.
            sys.stdout.write("\n".join(self.last_lines) + "\n")
            sys.stdout.flush()
