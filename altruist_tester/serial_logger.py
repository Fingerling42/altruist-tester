"""Raw serial logging."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from altruist_tester.artifacts import RunArtifacts


class SerialReader(Protocol):
    """Minimal serial reader protocol used by the logger."""

    def readline(self) -> bytes:
        """Read one line from the serial stream."""


@dataclass(frozen=True, slots=True)
class SerialLogStats:
    """Summary of one raw serial logging session."""

    lines_read: int
    bytes_read: int


def _decode_serial_line(line: bytes) -> str:
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def capture_raw_serial(
    serial_port: SerialReader,
    artifacts: RunArtifacts,
    duration_seconds: int,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> SerialLogStats:
    """Capture raw serial output until the requested duration elapses."""

    deadline = clock() + duration_seconds
    lines_read = 0
    bytes_read = 0

    with artifacts.serial_log.open("ab") as raw_log:
        while clock() < deadline:
            line = serial_port.readline()
            if not line:
                continue

            raw_log.write(line)
            raw_log.flush()
            lines_read += 1
            bytes_read += len(line)
            artifacts.append_event("serial_line", line=_decode_serial_line(line))

    return SerialLogStats(lines_read=lines_read, bytes_read=bytes_read)
