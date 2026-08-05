"""TOML configuration loading for tester profiles."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from altruist_tester.duration import DurationParseError, parse_duration_seconds
from altruist_tester.rules.defaults import (
    DEFAULT_NON_NEGATIVE_METRICS,
    DEFAULT_SENSOR_RANGES,
    SensorRange,
)
from altruist_tester.rules.uploads import UploadChannelConfig, UploadMode

BATCH_DEVICE_MODELS = frozenset({"urban", "insight"})


class ConfigError(ValueError):
    """Raised when a tester config file cannot be loaded or validated."""


@dataclass(frozen=True, slots=True)
class BatchDeviceConfig:
    """One device entry from a USB batch configuration."""

    slot: str
    port: Path
    model: str | None = None
    config: Path | None = None
    effective_config: Path | None = None
    expected_sensors: tuple[str, ...] = ()
    expected_metrics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BatchConfig:
    """Validated USB batch configuration loaded from TOML."""

    duration_input: str
    duration_seconds: int
    baud: int = 115200
    output_dir: Path = Path("runs")
    device_config: Path | None = None
    wait_port: bool = False
    wait_port_timeout_input: str = "2m"
    wait_port_timeout_seconds: int = 120
    devices: tuple[BatchDeviceConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class TesterConfig:
    """Validated tester configuration loaded from TOML.

    The object stores only normalized values used by the rules engine. Duration
    fields are converted to seconds during loading.
    """

    expected_sensors: tuple[str, ...] = ()
    expected_metrics: tuple[str, ...] = ()
    sensor_ranges: Mapping[str, SensorRange] = field(
        default_factory=lambda: dict(DEFAULT_SENSOR_RANGES)
    )
    warn_on_unknown_ranges: bool = False
    unknown_non_negative_metrics: tuple[str, ...] = tuple(
        sorted(DEFAULT_NON_NEGATIVE_METRICS)
    )
    flatline_window_seconds: int = 30 * 60
    flatline_fail_after_seconds: int = 60 * 60
    flatline_min_distinct_values: int = 2
    cadence_expected_interval_seconds: int = 5 * 60
    cadence_warn_after_missed: int = 2
    cadence_fail_after_missed: int = 4
    silence_warn_after_seconds: int = 2 * 60
    silence_fail_after_seconds: int = 10 * 60
    log_contract_startup_window_seconds: int = 10 * 60
    connectivity_upload: UploadChannelConfig = field(
        default_factory=UploadChannelConfig
    )
    datalog_upload: UploadChannelConfig = field(default_factory=UploadChannelConfig)

    @classmethod
    def defaults(cls) -> TesterConfig:
        """Return built-in tester defaults."""

        return cls()


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table")
    return value


def _optional_table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    return _require_mapping(value, name)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be an array of strings")
    return tuple(value)


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _required_string(value: object, name: str) -> str:
    string_value = _optional_string(value, name)
    if string_value is None:
        raise ConfigError(f"{name} is required")
    return string_value


def _bool_value(value: object, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


def _int_value(value: object, name: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _optional_non_negative_int_value(
    value: object,
    name: str,
    default: int | None,
) -> int | None:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{name} must be a non-negative integer")
    return value


def _ratio_value(value: object, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number between 0 and 1")
    ratio = float(value)
    if ratio < 0.0 or ratio > 1.0:
        raise ConfigError(f"{name} must be between 0 and 1")
    return ratio


def _upload_mode(value: object, name: str, default: UploadMode) -> UploadMode:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be disabled, optional, or required")
    mode = value.strip().lower()
    if mode not in {"disabled", "optional", "required"}:
        raise ConfigError(f"{name} must be disabled, optional, or required")
    return cast(UploadMode, mode)


def _number_value(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number")
    return float(value)


def _duration_value(value: object, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            raise ConfigError(f"{name} must be greater than zero")
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a duration string or seconds")
    try:
        return parse_duration_seconds(value)
    except DurationParseError as exc:
        raise ConfigError(f"{name}: {exc}") from exc


def _duration_input(value: object, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            raise ConfigError(f"{name} must be greater than zero")
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a duration string or seconds")
    return value.strip()


def _path_value(
    value: object,
    name: str,
    *,
    base_dir: Path | None = None,
    required: bool = False,
) -> Path | None:
    if value is None:
        if required:
            raise ConfigError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty path string")
    path = Path(value.strip())
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path


def _optional_model(value: object, name: str) -> str | None:
    model = _optional_string(value, name)
    if model is None:
        return None
    model = model.lower()
    if model not in BATCH_DEVICE_MODELS:
        allowed = ", ".join(sorted(BATCH_DEVICE_MODELS))
        raise ConfigError(f"{name} must be one of: {allowed}")
    return model


def _ensure_file_exists(path: Path, name: str) -> None:
    if not path.exists():
        raise ConfigError(f"{name} does not exist: {path}")
    if not path.is_file():
        raise ConfigError(f"{name} is not a file: {path}")


def _sensor_range_from_table(
    metric: str,
    value: object,
    default: SensorRange | None,
) -> SensorRange:
    table = _require_mapping(value, f"sensor_ranges.{metric}")
    name = table.get("name")
    if name is None:
        name = default.name if default is not None else metric
    if not isinstance(name, str) or not name:
        raise ConfigError(f"sensor_ranges.{metric}.name must be a non-empty string")

    minimum = _number_value(
        table.get("minimum", table.get("min")),
        f"sensor_ranges.{metric}.minimum",
    )
    maximum = _number_value(
        table.get("maximum", table.get("max")),
        f"sensor_ranges.{metric}.maximum",
    )
    unit = table.get("canonical_unit")
    if unit is not None and not isinstance(unit, str):
        raise ConfigError(f"sensor_ranges.{metric}.canonical_unit must be a string")
    non_negative = _bool_value(
        table.get("non_negative"),
        f"sensor_ranges.{metric}.non_negative",
        default.non_negative if default is not None else False,
    )

    if minimum is None and default is not None:
        minimum = default.minimum
    if maximum is None and default is not None:
        maximum = default.maximum
    if unit is None and default is not None:
        unit = default.canonical_unit

    return SensorRange(
        name=name,
        minimum=minimum,
        maximum=maximum,
        canonical_unit=unit,
        non_negative=non_negative,
    )


def _sensor_ranges(data: Mapping[str, Any]) -> Mapping[str, SensorRange]:
    # Config range tables override only the provided fields and inherit the
    # built-in defaults for everything else.
    ranges = dict(DEFAULT_SENSOR_RANGES)
    table = _optional_table(data, "sensor_ranges")
    for metric, value in table.items():
        ranges[metric] = _sensor_range_from_table(
            metric,
            value,
            ranges.get(metric),
        )
    return ranges


def _upload_channel_config(
    uploads: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    channel: str,
) -> UploadChannelConfig:
    return UploadChannelConfig(
        mode=_upload_mode(uploads.get(channel), f"uploads.{channel}", "disabled"),
        min_successes=_optional_non_negative_int_value(
            thresholds.get("min_successes"),
            f"uploads.{channel}_thresholds.min_successes",
            1,
        )
        or 0,
        min_success_rate=_ratio_value(
            thresholds.get("min_success_rate"),
            f"uploads.{channel}_thresholds.min_success_rate",
            0.8,
        ),
        max_consecutive_failures=_optional_non_negative_int_value(
            thresholds.get("max_consecutive_failures"),
            f"uploads.{channel}_thresholds.max_consecutive_failures",
            None,
        ),
    )


def _load_toml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    if not path.is_file():
        raise ConfigError(f"Config path is not a file: {path}")

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse config {path}: {exc}") from exc
    return data


def _batch_device_config(
    value: object,
    *,
    index: int,
    base_dir: Path,
    default_config: Path | None,
) -> BatchDeviceConfig:
    name = f"devices[{index}]"
    table = _require_mapping(value, name)
    config = _path_value(table.get("config"), f"{name}.config", base_dir=base_dir)
    return BatchDeviceConfig(
        slot=_required_string(table.get("slot"), f"{name}.slot"),
        port=cast(Path, _path_value(table.get("port"), f"{name}.port", required=True)),
        model=_optional_model(table.get("model"), f"{name}.model"),
        config=config,
        effective_config=config or default_config,
        expected_sensors=_string_tuple(
            table.get("expected_sensors"),
            f"{name}.expected_sensors",
        ),
        expected_metrics=_string_tuple(
            table.get("expected_metrics"),
            f"{name}.expected_metrics",
        ),
    )


def _validate_unique_batch_values(
    values: tuple[tuple[object, str], ...],
    *,
    name: str,
) -> None:
    seen: dict[object, str] = {}
    for value, slot in values:
        previous_slot = seen.get(value)
        if previous_slot is not None:
            raise ConfigError(
                f"Duplicate device {name}: {value} used by {previous_slot} and {slot}"
            )
        seen[value] = slot


def validate_batch_config(config: BatchConfig) -> None:
    """Validate an already constructed USB batch configuration.

    :raises ConfigError: If slots, ports, profile paths, or mixed-model config
        rules are invalid.
    """

    if config.device_config is not None:
        _ensure_file_exists(config.device_config, "batch.device_config")

    for index, device in enumerate(config.devices):
        name = f"devices[{index}]"
        if device.config is not None:
            _ensure_file_exists(device.config, f"{name}.config")
        if device.effective_config is None:
            raise ConfigError(
                f"{name}.config is required because no batch.device_config is set"
            )

    _validate_unique_batch_values(
        tuple((device.slot, device.slot) for device in config.devices),
        name="slot",
    )
    _validate_unique_batch_values(
        tuple((device.port, device.slot) for device in config.devices),
        name="port",
    )

    models = {device.model for device in config.devices if device.model is not None}
    if len(models) > 1:
        devices_without_config = [
            device.slot for device in config.devices if device.config is None
        ]
        if devices_without_config:
            slots = ", ".join(devices_without_config)
            raise ConfigError(
                "Mixed device models require per-device config for every slot; "
                f"missing config for: {slots}"
            )


def load_batch_config(path: Path) -> BatchConfig:
    """Load a USB batch TOML configuration.

    :param path: Path to a batch TOML file.
    :returns: A normalized ``BatchConfig`` with duration converted to seconds
        and per-device effective profile paths resolved.
    :raises ConfigError: If the file is missing, malformed, or does not match
        the batch config shape.
    """

    data = _load_toml(path)
    base_dir = path.parent
    batch = _require_mapping(data.get("batch"), "batch")
    duration_input = _duration_input(batch.get("duration"), "batch.duration")
    duration_seconds = _duration_value(
        batch.get("duration"),
        "batch.duration",
        0,
    )
    default_config = _path_value(
        batch.get("device_config"),
        "batch.device_config",
        base_dir=base_dir,
    )
    devices_value = data.get("devices")
    if not isinstance(devices_value, list):
        raise ConfigError("devices must be an array of tables")
    if not devices_value:
        raise ConfigError("devices must contain at least one device")

    config = BatchConfig(
        duration_input=duration_input,
        duration_seconds=duration_seconds,
        baud=_int_value(batch.get("baud"), "batch.baud", 115200),
        output_dir=cast(
            Path,
            _path_value(batch.get("output_dir"), "batch.output_dir") or Path("runs"),
        ),
        device_config=default_config,
        wait_port=_bool_value(batch.get("wait_port"), "batch.wait_port", False),
        wait_port_timeout_input=_duration_input(
            batch.get("wait_port_timeout", "2m"),
            "batch.wait_port_timeout",
        ),
        wait_port_timeout_seconds=_duration_value(
            batch.get("wait_port_timeout", "2m"),
            "batch.wait_port_timeout",
            120,
        ),
        devices=tuple(
            _batch_device_config(
                device,
                index=index,
                base_dir=base_dir,
                default_config=default_config,
            )
            for index, device in enumerate(devices_value)
        ),
    )
    validate_batch_config(config)
    return config


def load_tester_config(path: Path | None) -> TesterConfig:
    """Load and validate a tester TOML profile.

    :param path: Optional path to a TOML config file. ``None`` returns built-in
        defaults.
    :returns: A normalized ``TesterConfig`` with duration values converted to
        seconds.
    :raises ConfigError: If the file is missing, malformed, or contains values
        with invalid types.
    """

    if path is None:
        return TesterConfig.defaults()

    data = _load_toml(path)
    expect = _optional_table(data, "expect")
    range_checks = _optional_table(data, "range_checks")
    flatline = _optional_table(data, "flatline")
    cadence = _optional_table(data, "cadence")
    serial = _optional_table(data, "serial")
    log_contract = _optional_table(data, "log_contract")
    uploads = _optional_table(data, "uploads")
    connectivity_thresholds = _require_mapping(
        uploads.get("connectivity_thresholds", {}),
        "uploads.connectivity_thresholds",
    )
    datalog_thresholds = _require_mapping(
        uploads.get("datalog_thresholds", {}),
        "uploads.datalog_thresholds",
    )

    return TesterConfig(
        expected_sensors=_string_tuple(expect.get("sensors"), "expect.sensors"),
        expected_metrics=_string_tuple(expect.get("metrics"), "expect.metrics"),
        sensor_ranges=_sensor_ranges(data),
        warn_on_unknown_ranges=_bool_value(
            range_checks.get("warn_on_unknown"),
            "range_checks.warn_on_unknown",
            False,
        ),
        unknown_non_negative_metrics=_string_tuple(
            range_checks.get("unknown_non_negative_metrics"),
            "range_checks.unknown_non_negative_metrics",
        )
        or tuple(sorted(DEFAULT_NON_NEGATIVE_METRICS)),
        flatline_window_seconds=_duration_value(
            flatline.get("window"),
            "flatline.window",
            30 * 60,
        ),
        flatline_fail_after_seconds=_duration_value(
            flatline.get("fail_after"),
            "flatline.fail_after",
            60 * 60,
        ),
        flatline_min_distinct_values=_int_value(
            flatline.get("min_distinct_values"),
            "flatline.min_distinct_values",
            2,
        ),
        cadence_expected_interval_seconds=_duration_value(
            cadence.get("expected_interval"),
            "cadence.expected_interval",
            5 * 60,
        ),
        cadence_warn_after_missed=_int_value(
            cadence.get("warn_after_missed"),
            "cadence.warn_after_missed",
            2,
        ),
        cadence_fail_after_missed=_int_value(
            cadence.get("fail_after_missed"),
            "cadence.fail_after_missed",
            4,
        ),
        silence_warn_after_seconds=_duration_value(
            serial.get("silence_warn_after"),
            "serial.silence_warn_after",
            2 * 60,
        ),
        silence_fail_after_seconds=_duration_value(
            serial.get("silence_fail_after"),
            "serial.silence_fail_after",
            10 * 60,
        ),
        log_contract_startup_window_seconds=_duration_value(
            log_contract.get("startup_window"),
            "log_contract.startup_window",
            10 * 60,
        ),
        connectivity_upload=_upload_channel_config(
            uploads,
            connectivity_thresholds,
            "connectivity",
        ),
        datalog_upload=_upload_channel_config(
            uploads,
            datalog_thresholds,
            "datalog",
        ),
    )
