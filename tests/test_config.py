from pathlib import Path

import pytest

from altruist_tester.config import (
    BatchConfig,
    ConfigError,
    load_batch_config,
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

[uploads]
connectivity = "required"
datalog = "optional"

[uploads.connectivity_thresholds]
min_successes = 2
min_success_rate = 0.75
max_consecutive_failures = 4

[uploads.datalog_thresholds]
min_successes = 1
min_success_rate = 0.5

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
    assert config.connectivity_upload.mode == "required"
    assert config.connectivity_upload.min_successes == 2
    assert config.connectivity_upload.min_success_rate == 0.75
    assert config.connectivity_upload.max_consecutive_failures == 4
    assert config.datalog_upload.mode == "optional"
    assert config.datalog_upload.min_success_rate == 0.5
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


def test_load_tester_config_rejects_invalid_upload_mode(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(
        """
[uploads]
connectivity = "sure"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="uploads.connectivity"):
        load_tester_config(path)


def test_urban_example_config_loads():
    config = load_tester_config(Path("configs/urban.example.toml"))

    assert config.expected_sensors == ("bme280", "sds", "ics-43434")
    assert config.sensor_ranges["noiseAvg"].maximum == 130.0


def test_insight_example_config_loads():
    config = load_tester_config(Path("configs/insight.example.toml"))

    assert config.expected_sensors == ("scd41", "bme680")
    assert config.sensor_ranges["co2"].maximum == 10000.0


def test_load_batch_config_reads_mixed_device_profiles(tmp_path):
    urban_config = tmp_path / "urban.toml"
    insight_config = tmp_path / "insight.toml"
    urban_config.touch()
    insight_config.touch()
    path = tmp_path / "batch.toml"
    path.write_text(
        """
[batch]
duration = "24h"
baud = 9600
output_dir = "batch-runs"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/urban"
config = "urban.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/insight"
config = "insight.toml"
expected_sensors = ["scd41"]
expected_metrics = ["co2"]
""",
        encoding="utf-8",
    )

    config = load_batch_config(path)

    assert config == BatchConfig(
        duration_input="24h",
        duration_seconds=24 * 60 * 60,
        baud=9600,
        output_dir=Path("batch-runs"),
        devices=config.devices,
    )
    assert [device.slot for device in config.devices] == ["slot-01", "slot-02"]
    assert [device.model for device in config.devices] == ["urban", "insight"]
    assert config.devices[0].port == Path("/dev/serial/by-path/urban")
    assert config.devices[0].config == urban_config
    assert config.devices[0].effective_config == urban_config
    assert config.devices[1].config == insight_config
    assert config.devices[1].effective_config == insight_config
    assert config.devices[1].expected_sensors == ("scd41",)
    assert config.devices[1].expected_metrics == ("co2",)


def test_load_batch_config_uses_default_device_config_for_homogeneous_batch(tmp_path):
    default_config = tmp_path / "urban.toml"
    default_config.touch()
    path = tmp_path / "batch.toml"
    path.write_text(
        """
[batch]
duration = "2h"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/slot-01"
""",
        encoding="utf-8",
    )

    config = load_batch_config(path)

    assert config.device_config == default_config
    assert config.devices[0].config is None
    assert config.devices[0].effective_config == default_config


def test_load_batch_config_rejects_missing_duration(tmp_path):
    path = tmp_path / "batch.toml"
    path.write_text(
        """
[batch]

[[devices]]
slot = "slot-01"
port = "/dev/serial/by-path/slot-01"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="batch.duration"):
        load_batch_config(path)


def test_load_batch_config_rejects_missing_devices(tmp_path):
    path = tmp_path / "batch.toml"
    path.write_text(
        """
[batch]
duration = "1h"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="devices"):
        load_batch_config(path)


def test_batch_usb_example_config_loads():
    config = load_batch_config(Path("configs/batch.usb.example.toml"))

    assert config.duration_seconds == 24 * 60 * 60
    assert [device.model for device in config.devices] == ["urban", "insight"]
    assert config.devices[0].effective_config == Path("configs/urban.example.toml")
    assert config.devices[1].effective_config == Path("configs/insight.example.toml")
