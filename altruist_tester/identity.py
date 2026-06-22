"""Device identity helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altruist_tester.ports import SerialPortInfo

_DEVICE_ID_RE = re.compile(
    r"(?i)(?:chip\s*id|chipid|sensor_id)\D*"
    r"([0-9a-f][0-9a-f:.-]{10,}[0-9a-f])"
)
_MAC_IN_TEXT_RE = re.compile(r"(?i)([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})")
_PLAIN_DEVICE_ID_RE = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{12})(?![0-9a-f])")
_HEX_RE = re.compile(r"[^0-9A-Fa-f]+")


def normalize_device_id(value: str | None) -> str | None:
    """Normalize an Altruist MAC/chip id to uppercase 12-hex form.

    :param value: Candidate id, optionally separated by ``:``, ``-``, or other
        punctuation.
    :returns: Normalized id when the input contains exactly 12 hexadecimal
        characters, otherwise ``None``.
    """

    if value is None:
        return None

    normalized = _HEX_RE.sub("", value).upper()
    if len(normalized) != 12:
        return None
    return normalized


def format_device_id_as_mac(device_id: str | None) -> str | None:
    """Format a normalized device id as a colon-separated MAC address."""

    normalized = normalize_device_id(device_id)
    if normalized is None:
        return None
    return ":".join(normalized[index : index + 2] for index in range(0, 12, 2))


def parse_identity_from_serial_line(line: str) -> str | None:
    """Parse a device id from known firmware serial identity lines.

    Current firmware prints ``ChipId`` during setup and exposes the same value
    as ``sensor_id`` in JSON payloads. Both forms are accepted here.
    """

    match = _DEVICE_ID_RE.search(line)
    if match is None:
        return None
    return normalize_device_id(match.group(1))


def _path_identity(path: Path) -> str | None:
    text = path.name
    mac_match = _MAC_IN_TEXT_RE.search(text)
    if mac_match is not None:
        return normalize_device_id(mac_match.group(1))

    plain_match = _PLAIN_DEVICE_ID_RE.search(text)
    if plain_match is not None:
        return normalize_device_id(plain_match.group(1))

    return None


def _matching_symlink(directory: Path, target: Path) -> str | None:
    try:
        resolved_target = target.resolve(strict=True)
    except OSError:
        return None

    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return None

    for entry in entries:
        try:
            if entry.resolve(strict=True) == resolved_target:
                return str(entry)
        except OSError:
            continue
    return None


def _find_port_info(
    port: Path,
    port_infos: Iterable[SerialPortInfo],
) -> SerialPortInfo | None:
    port_text = str(port)
    try:
        resolved_port = port.resolve(strict=True)
    except OSError:
        resolved_port = None

    for port_info in port_infos:
        if port_info.device == port_text:
            return port_info
        if resolved_port is None:
            continue
        try:
            if Path(port_info.device).resolve(strict=True) == resolved_port:
                return port_info
        except OSError:
            continue
    return None


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Best-known identity for one connected device."""

    port: str
    port_resolved: str | None = None
    by_id: str | None = None
    by_path: str | None = None
    usb_serial: str | None = None
    usb_device_id: str | None = None
    serial_log_device_id: str | None = None
    device_id: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    conflicts: tuple[dict[str, str], ...] = ()

    @property
    def mac(self) -> str | None:
        """Return the best-known identity as a MAC address."""

        return format_device_id_as_mac(self.device_id)

    def with_serial_log_device_id(
        self,
        serial_log_device_id: str | None,
    ) -> DeviceIdentity:
        """Return a copy enriched with identity parsed from serial logs."""

        normalized = normalize_device_id(serial_log_device_id)
        if normalized is None:
            return self

        sources = {**self.sources, "serial_log": normalized}
        return _build_identity(
            port=self.port,
            port_resolved=self.port_resolved,
            by_id=self.by_id,
            by_path=self.by_path,
            usb_serial=self.usb_serial,
            sources=sources,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly identity payload."""

        return {
            "device_id": self.device_id,
            "mac": self.mac,
            "port": self.port,
            "port_resolved": self.port_resolved,
            "by_id": self.by_id,
            "by_path": self.by_path,
            "usb_serial": self.usb_serial,
            "usb_device_id": self.usb_device_id,
            "serial_log_device_id": self.serial_log_device_id,
            "sources": dict(sorted(self.sources.items())),
            "conflicts": list(self.conflicts),
        }


def _build_identity(
    *,
    port: str,
    port_resolved: str | None,
    by_id: str | None,
    by_path: str | None,
    usb_serial: str | None,
    sources: dict[str, str],
) -> DeviceIdentity:
    unique_values = sorted(set(sources.values()))
    device_id = unique_values[0] if len(unique_values) == 1 else None
    conflicts: tuple[dict[str, str], ...] = ()
    if len(unique_values) > 1:
        conflicts = tuple(
            {"source": source, "device_id": value}
            for source, value in sorted(sources.items())
        )

    return DeviceIdentity(
        port=port,
        port_resolved=port_resolved,
        by_id=by_id,
        by_path=by_path,
        usb_serial=usb_serial,
        usb_device_id=sources.get("usb"),
        serial_log_device_id=sources.get("serial_log"),
        device_id=device_id,
        sources=sources,
        conflicts=conflicts,
    )


def detect_device_identity(
    port: Path,
    *,
    port_infos: Iterable[SerialPortInfo] = (),
) -> DeviceIdentity:
    """Detect device identity from USB metadata and stable serial symlinks."""

    port_text = str(port)
    try:
        port_resolved = str(port.resolve(strict=True))
    except OSError:
        port_resolved = None

    by_id = str(port) if "/dev/serial/by-id/" in port_text else None
    by_path = str(port) if "/dev/serial/by-path/" in port_text else None
    if by_id is None:
        by_id = _matching_symlink(Path("/dev/serial/by-id"), port)
    if by_path is None:
        by_path = _matching_symlink(Path("/dev/serial/by-path"), port)

    port_info = _find_port_info(port, port_infos)
    usb_serial = port_info.serial_number if port_info is not None else None
    sources = {}
    usb_device_id = normalize_device_id(usb_serial)
    if usb_device_id is None and by_id is not None:
        usb_device_id = _path_identity(Path(by_id))
    if usb_device_id is None:
        usb_device_id = _path_identity(port)
    if usb_device_id is not None:
        sources["usb"] = usb_device_id

    return _build_identity(
        port=port_text,
        port_resolved=port_resolved,
        by_id=by_id,
        by_path=by_path,
        usb_serial=usb_serial,
        sources=sources,
    )
