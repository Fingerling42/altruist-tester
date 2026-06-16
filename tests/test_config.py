from pathlib import Path

import pytest

from altruist_tester.config import (
    ConfigError,
    load_tester_config,
)
from altruist_tester.config import (
    TesterConfig as ProfileConfig,
)


def test_load_tester_config_returns_defaults_without_path():
    config = load_tester_config(None)

    assert config == ProfileConfig.defaults()
    assert config.silence_warn_after_seconds == 2 * 60
    assert config.cadence_expected_interval_seconds == 5 * 60


def test_load_tester_config_reads_expected_sensors_and_thresholds(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(
        """
[expect]
sensors = ["bme280", "sds"]
metrics = ["temperature"]

[flatline]
window = "2m"
fail_after = "10m"
min_distinct_values = 3

[cadence]
expected_interval = "30s"
warn_after_missed = 3
fail_after_missed = 5

[serial]
silence_warn_after = "15s"
silence_fail_after = "45s"

[sensor_ranges.temperature]
minimum = -10.0
maximum = 60.0
""",
        encoding="utf-8",
    )

    config = load_tester_config(path)

    assert config.expected_sensors == ("bme280", "sds")
    assert config.expected_metrics == ("temperature",)
    assert config.flatline_window_seconds == 120
    assert config.flatline_fail_after_seconds == 600
    assert config.flatline_min_distinct_values == 3
    assert config.cadence_expected_interval_seconds == 30
    assert config.cadence_warn_after_missed == 3
    assert config.cadence_fail_after_missed == 5
    assert config.silence_warn_after_seconds == 15
    assert config.silence_fail_after_seconds == 45
    assert config.sensor_ranges["temperature"].minimum == -10.0
    assert config.sensor_ranges["temperature"].maximum == 60.0


def test_load_tester_config_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Config file does not exist"):
        load_tester_config(tmp_path / "missing.toml")


def test_load_tester_config_rejects_invalid_duration(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(
        """
[serial]
silence_fail_after = "soon"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="serial.silence_fail_after"):
        load_tester_config(path)


def test_urban_example_config_loads():
    config = load_tester_config(Path("configs/urban.example.toml"))

    assert config.expected_sensors == ("bme280", "sds", "ics-43434")
    assert config.sensor_ranges["noiseAvg"].maximum == 130.0


def test_insight_example_config_loads():
    config = load_tester_config(Path("configs/insight.example.toml"))

    assert config.expected_sensors == ("scd41", "bme680")
    assert config.sensor_ranges["co2"].maximum == 10000.0
