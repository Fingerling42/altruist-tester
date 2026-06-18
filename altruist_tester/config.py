"""TOML configuration loading for tester profiles."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altruist_tester.duration import DurationParseError, parse_duration_seconds
from altruist_tester.rules.defaults import (
    DEFAULT_NON_NEGATIVE_METRICS,
    DEFAULT_SENSOR_RANGES,
    SensorRange,
)


class ConfigError(ValueError):
    """Raised when a tester config file cannot be loaded or validated."""


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
    )
