"""Serial port discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from serial.tools import list_ports


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    """Human-readable metadata for one detected USB serial port."""

    device: str
    description: str | None = None
    hwid: str | None = None
    vid: int | None = None
    pid: int | None = None
    manufacturer: str | None = None

    @property
    def vid_pid(self) -> str:
        """Return VID:PID if both values are available."""

        if self.vid is None or self.pid is None:
            return ""
        return f"{self.vid:04X}:{self.pid:04X}"


def _is_usb_serial_candidate(device: str) -> bool:
    name = Path(device).name
    return (
        name.startswith("ttyACM")
        or name.startswith("ttyUSB")
        or name.startswith("cu.usbmodem")
        or name.startswith("cu.usbserial")
    )


def list_serial_ports() -> list[SerialPortInfo]:
    """List likely USB serial ports in deterministic order.

    The tester only auto-discovers common USB CDC/serial device names. Callers
    can still pass any explicit port path to the CLI with ``--port``.
    """

    ports = [
        SerialPortInfo(
            device=port.device,
            description=port.description,
            hwid=port.hwid,
            vid=port.vid,
            pid=port.pid,
            manufacturer=port.manufacturer,
        )
        for port in list_ports.comports()
        if _is_usb_serial_candidate(port.device)
    ]
    return sorted(ports, key=lambda port: port.device)
