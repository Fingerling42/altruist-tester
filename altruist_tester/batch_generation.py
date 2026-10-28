"""Generate ordinary batch TOML without opening serial ports."""

import json
import os
from pathlib import Path

from altruist_tester.config import ConfigError, load_tester_config, parse_mac
from altruist_tester.identity import (
    detect_device_identity,
    format_device_id_as_mac,
    normalize_device_id,
)
from altruist_tester.ports import SerialPortInfo


def generate_batch_toml(
    groups: list[tuple[str, list[str], Path | None]],
    *,
    destination: Path,
    duration: str,
    port_template: str | None,
    port_infos: list[SerialPortInfo],
) -> str:
    """Resolve connected USB identities or require an explicit pending path."""
    lines = [
        "[batch]",
        f"duration = {json.dumps(duration, ensure_ascii=False)}",
        "wait_port = true",
        '# Use "infinite" explicitly when devices may be connected at any time.',
        'wait_port_timeout = "2m"',
    ]
    seen_macs: set[str] = set()
    seen_ports: set[Path] = set()
    for model, values, profile in groups:
        if not values:
            continue
        if profile is None:
            raise ConfigError(f"--{model}-profile is required for {model} MACs")
        load_tester_config(profile)
        macs = sorted(parse_mac(value) for value in values)
        for device_id in macs:
            if device_id in seen_macs:
                raise ConfigError(f"Duplicate MAC: {device_id}")
            seen_macs.add(device_id)
            matches = {}
            for info in port_infos:
                identity = detect_device_identity(Path(info.device), port_infos=[info])
                if identity.usb_device_id == device_id:
                    matches[Path(info.device).resolve()] = identity
            if len(matches) > 1:
                raise ConfigError(f"Ambiguous USB ports for MAC {device_id}")
            if matches:
                identity = next(iter(matches.values()))
                port = Path(identity.by_id or identity.port).absolute()
                if normalize_device_id(identity.usb_serial) != device_id:
                    raise ConfigError(
                        f"USB/by-id path found for {device_id}, but matching "
                        "USB serial metadata is unavailable; use a manual batch"
                    )
                mapping = "USB identity verified during generation"
            else:
                if port_template is None:
                    raise ConfigError(
                        f"No USB port found for MAC {device_id}; "
                        "connect it or supply --port-template"
                    )
                if not any(
                    field in port_template
                    for field in ("{mac}", "{mac_lower}", "{id}", "{id_lower}")
                ):
                    raise ConfigError(
                        "Port template must contain "
                        "{mac}, {mac_lower}, {id} or {id_lower}"
                    )
                try:
                    port = Path(
                        port_template.format(
                            mac=format_device_id_as_mac(device_id),
                            mac_lower=format_device_id_as_mac(device_id).lower(),
                            id=device_id,
                            id_lower=device_id.lower(),
                        )
                    )
                except (ValueError, KeyError, IndexError) as exc:
                    raise ConfigError(f"Invalid port template: {exc}") from exc
                if not port.is_absolute():
                    raise ConfigError("Port template must produce an absolute path")
                if port.exists():
                    raise ConfigError(
                        f"Port exists but its USB identity was not verified: {port}"
                    )
                mapping = (
                    "UNVERIFIED expected path; USB serial will be checked at startup"
                )
            resolved_port = port.resolve()
            if resolved_port in seen_ports:
                raise ConfigError(f"Duplicate device port: {port}")
            seen_ports.add(resolved_port)
            relative_profile = os.path.relpath(
                profile.resolve(), destination.parent.resolve()
            )
            lines.extend(
                [
                    "",
                    f"# {mapping}",
                    "[[devices]]",
                    f"slot = {json.dumps(f'{model}-{device_id.lower()}')}",
                    f"model = {json.dumps(model)}",
                    f"port = {json.dumps(str(port), ensure_ascii=False)}",
                    f"config = {json.dumps(relative_profile, ensure_ascii=False)}",
                    f"expected_mac = {json.dumps(format_device_id_as_mac(device_id))}",
                ]
            )
    if not seen_macs:
        raise ConfigError("Specify at least one --urban-mac or --insight-mac")
    return "\n".join(lines) + "\n"
