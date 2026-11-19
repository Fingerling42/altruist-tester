"""Synthetic wire examples: no serial devices, network or firmware required."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from altruist_tester.cli import app
from altruist_tester.config import ConfigError, load_tester_config
from altruist_tester.sensor_contract import METRICS
from altruist_tester.sensor_observations import (
    SensorObservationTracker,
    parse_sensor_observation,
)
from tests.helpers import uart_settings, uart_snapshot

NOW = datetime(2026, 11, 18, tzinfo=UTC)


def fixture(sensor="BME280"):
    return uart_snapshot(sensor, {m: 1 for m in METRICS[sensor]})


def feed(tracker, data):
    return tracker.feed(json.dumps(data), NOW)


@pytest.mark.parametrize("sensor", sorted(METRICS))
@pytest.mark.parametrize("state", ["never", "warming", "waiting", "error", "ok"])
def test_every_sensor_and_state(sensor, state):
    data = fixture(sensor)
    meta = data["_observations"]
    if sensor == "ICS43434":
        data = json.loads(
            (Path(__file__).parent / "fixtures/uart_v2_noise.json").read_text()
        )
        meta = data["_observations"]
    if state != "ok":
        meta.update(
            state=state, reason="init_failed" if state == "error" else "sds_active"
        )
        meta["cycle_seq"] = 0
        for channel in meta["channels"].values():
            channel.update(
                cycles_total=0,
                ok_total=0,
                failed_total=0,
                last_ok_ms=None,
                last_failed_ms=None,
                last_recovered_ms=None,
                state=state,
                reason=meta["reason"],
            )
        for item in data[sensor].values():
            item.update(
                value=None, observation=dict(seq=0, measured_ms=None, state=state)
            )
        if sensor == "ICS43434":
            meta["failures"] = dict(next_seq=1, recent=[])
            meta["noise"].update(successful_blocks_total=0, last_ok_ms=None)
            meta["noise"]["interval"].update(
                max_gap_active_ms=310000,
                max_gap_end_ms=330000,
                open_gap_active_ms=310000,
            )
            meta["noise"]["window_current"]["successful_blocks"] = 0
            for w in meta["noise"]["windows"]:
                w["successful_blocks"] = 0
    parsed = parse_sensor_observation(json.dumps(data))
    assert parsed["state"] == state
    tracker = SensorObservationTracker(expected_settings={sensor: parsed["settings"]})
    event, samples = feed(tracker, data)
    assert "invalid_reason" not in event
    assert len(samples) == (len(METRICS[sensor]) if state == "ok" else 0)


@pytest.mark.parametrize(
    "path,value",
    [
        (("version",), 1),
        (("version",), True),
        (("snapshot_seq",), True),
        (("time_ms",), 2**42),
        (("cycle_seq",), -1),
        (("state",), []),
        (("channels", "main", "cycles_total"), 2),
        (("channels", "main", "ok_total"), True),
        (("channels", "main", "last_ok_ms"), None),
        (("channels", "main", "last_recovered_ms"), 1001),
        (("channels", "main", "last_failed_ms"), 1000),
        (("failures", "next_seq"), 2),
        (("failures", "recent"), {}),
        (("settings", "gas_enabled"), False),
        (("settings", "publication"), "heartbeat"),
        (("settings", "measurement_interval_ms"), 10),
        (("settings", "fetch_wait_ms"), False),
    ],
)
def test_invalid_known_fields_reject_atomically(path, value):
    data = fixture()
    target = data["_observations"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    tracker = SensorObservationTracker()
    event, samples = feed(tracker, data)
    assert event.get("invalid_reason")
    assert not samples and not tracker.latest and not tracker._snapshots


@pytest.mark.parametrize(
    "field", ["settings", "channels", "failures", "cycle_seq", "reason"]
)
def test_missing_required_objects_fail(field):
    data = fixture()
    del data["_observations"][field]
    assert feed(SensorObservationTracker(), data)[0].get("invalid_reason")


@pytest.mark.parametrize("sensor", sorted(METRICS))
def test_missing_expected_metric_fails_before_first_baseline(sensor):
    data = fixture(sensor)
    data[sensor].pop(next(iter(data[sensor])))
    assert feed(SensorObservationTracker(), data)[0].get("invalid_reason")


def failed(data, *, at=2000, recovered=False):
    data = copy.deepcopy(data)
    meta = data["_observations"]
    meta.update(
        time_ms=at, snapshot_seq=2, cycle_seq=2, state="error", reason="read_failed"
    )
    meta["channels"]["main"].update(
        cycles_total=2,
        failed_total=1,
        last_failed_ms=at,
        state="error",
        reason="read_failed",
    )
    meta["failures"] = dict(
        next_seq=2,
        recent=[
            dict(seq=1, cycle_seq=2, at_ms=at, channels=["main"], reason="read_failed")
        ],
    )
    for item in data[meta["sensor"]].values():
        item["observation"]["state"] = "error"
    if recovered:
        meta.update(
            time_ms=at + 1000, snapshot_seq=3, cycle_seq=3, state="ok", reason="none"
        )
        meta["channels"]["main"].update(
            cycles_total=3,
            ok_total=2,
            last_ok_ms=at + 1000,
            last_recovered_ms=at + 1000,
            state="ok",
            reason="none",
        )
        for item in data[meta["sensor"]].values():
            item["observation"].update(seq=2, measured_ms=at + 1000, state="ok")
    return data


def test_failure_cache_recovery_and_repeated_history():
    tracker = SensorObservationTracker()
    data = fixture()
    assert len(feed(tracker, data)[1]) == 3
    assert feed(tracker, failed(data))[1] == []
    recovered = failed(data, recovered=True)
    assert len(feed(tracker, recovered)[1]) == 3
    recovered["_observations"].update(snapshot_seq=4, time_ms=4000)
    assert feed(tracker, recovered)[1] == []
    assert not tracker.issues
    assert tracker.as_dict()["sensors"][0]["failures"]["next_seq"] == 2


def test_partial_gas_error_keeps_environment_samples():
    data = fixture("BME680")
    meta = data["_observations"]
    meta.update(state="error", reason="gas_invalid")
    meta["channels"]["gas"].update(
        ok_total=0,
        failed_total=1,
        last_ok_ms=None,
        last_failed_ms=1000,
        state="error",
        reason="gas_invalid",
    )
    meta["failures"] = dict(
        next_seq=2,
        recent=[
            dict(seq=1, cycle_seq=1, at_ms=1000, channels=["gas"], reason="gas_invalid")
        ],
    )
    for metric in METRICS["BME680"] - {"temperature", "humidity", "pressure"}:
        data["BME680"][metric].update(
            value=None, observation=dict(seq=0, measured_ms=None, state="error")
        )
    event, samples = feed(SensorObservationTracker(), data)
    assert not event.get("invalid_reason")
    assert {s.metric for s in samples} == {"temperature", "humidity", "pressure"}


def test_failure_history_cannot_be_rewritten_even_without_counter_delta():
    tracker = SensorObservationTracker()
    data = failed(fixture(), recovered=True)
    feed(tracker, data)
    data["_observations"].update(snapshot_seq=4, time_ms=4000)
    data["_observations"]["failures"]["recent"][0]["reason"] = "different"
    assert feed(tracker, data)[0] == {"invalid_reason": "failure_history_changed"}
    assert tracker._snapshots["BME280"]["snapshot_seq"] == 3


def test_unrecovered_error_cannot_be_hidden_by_waiting():
    tracker = SensorObservationTracker()
    data = failed(fixture())
    feed(tracker, data)
    data["_observations"].update(snapshot_seq=3, time_ms=3000, state="waiting")
    data["_observations"]["channels"]["main"].update(state="waiting", reason="waiting")
    assert feed(tracker, data)[0].get("invalid_reason")


@pytest.mark.parametrize("clock_value", [True, -1, "1.0", 1.0, "-1", "9" * 17, 2**53])
def test_counter_boundaries(clock_value):
    data = fixture()
    data["_observations"]["snapshot_seq"] = clock_value
    assert feed(SensorObservationTracker(), data)[0].get("invalid_reason")


def test_duplicate_json_keys_overflow_and_oversize_fail():
    data = json.dumps(fixture()).replace('"version": 2', '"version": 2, "version": 2')
    with pytest.raises(ValueError, match="duplicate_json_key"):
        parse_sensor_observation(data)
    with pytest.raises(ValueError, match="snapshot_overflow"):
        parse_sensor_observation(
            '{"_observations":{"version":2,"error":"snapshot_overflow"}}'
        )
    with pytest.raises(ValueError, match="line_too_long"):
        parse_sensor_observation(json.dumps(fixture()) + " " * 8192)


@pytest.fixture
def noise():
    return json.loads(
        (Path(__file__).parent / "fixtures/uart_v2_noise.json").read_text()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("active_ms_total", 310001),
        ("successful_blocks_total", 1501),
        ("last_ok_ms", 329899),
        ("started_ms", 1000),
        ("windows", []),
        ("window_current", None),
        ("interval", None),
        ("sds_pause", None),
    ],
)
def test_noise_contradictions(noise, field, value):
    noise["_observations"]["noise"][field] = value
    assert feed(SensorObservationTracker(), noise)[0].get("invalid_reason")


def test_noise_pauses_and_windows_are_immutable(noise):
    tracker = SensorObservationTracker()
    assert not feed(tracker, noise)[0].get("invalid_reason")
    meta = noise["_observations"]
    meta.update(snapshot_seq=13, time_ms=331000)
    n = meta["noise"]
    n["active_ms_total"] = 311000
    n["interval"].update(
        start_ms=330000,
        end_ms=331000,
        max_gap_active_ms=1100,
        max_gap_end_ms=331000,
        open_gap_active_ms=1100,
    )
    n["window_current"]["active_ms"] = 11000
    assert not feed(tracker, noise)[0].get("invalid_reason")
    meta.update(snapshot_seq=14, time_ms=332000)
    n["active_ms_total"] = 312000
    n["interval"].update(
        start_ms=331000,
        end_ms=332000,
        max_gap_active_ms=2100,
        max_gap_end_ms=332000,
        open_gap_active_ms=2100,
    )
    n["window_current"]["active_ms"] = 12000
    n["windows"][0]["successful_blocks"] = 1469
    n["window_current"]["successful_blocks"] = 31
    assert feed(tracker, noise)[0] == {"invalid_reason": "noise_window_changed"}


@pytest.mark.parametrize("mutation", ["reenter", "budget", "same_cycle_start"])
def test_noise_pause_abuse(noise, mutation):
    tracker = SensorObservationTracker()
    feed(tracker, noise)
    meta = noise["_observations"]
    meta.update(snapshot_seq=13, time_ms=331000)
    n = meta["noise"]
    n["active_ms_total"] = 311000
    n["window_current"]["active_ms"] = 11000
    n["interval"].update(
        start_ms=330000,
        end_ms=331000,
        max_gap_active_ms=1100,
        max_gap_end_ms=331000,
        open_gap_active_ms=1100,
    )
    if mutation == "reenter":
        n["sds_pause"]["state"] = "active"
    elif mutation == "budget":
        n["sds_pause"]["excluded_ms_total"] = 20001
    else:
        n["sds_pause"]["started_ms"] = 100001
    assert feed(tracker, noise)[0].get("invalid_reason")


def profile_text(sensor="BME280"):
    settings = uart_settings(sensor)
    lines = [
        "[observations]",
        'firmware_source = "test firmware contract v2"',
        'settings_rule = "explicit synthetic effective settings"',
        f"[observations.settings.{sensor}]",
    ]
    for key, value in settings.items():
        if value is not None:
            lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines)


def test_explicit_profile_normalizes_toml_nulls_and_rejects_invalid_settings(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(profile_text())
    config = load_tester_config(path)
    assert config.observation_settings == {"BME280": uart_settings("BME280")}
    path.write_text(
        profile_text().replace("fetch_wait_ms = 100", "fetch_wait_ms = true")
    )
    with pytest.raises(ConfigError, match="invalid_integer"):
        load_tester_config(path)


@pytest.mark.parametrize("changed", [False, True])
def test_settings_mismatch_stops_capture_and_keeps_reviewable_artifacts(
    tmp_path, monkeypatch, changed
):
    from altruist_tester.serial_logger import SerialLogStats, capture_raw_serial
    from tests.cli_helpers import patch_cli_capture
    from tests.test_serial_logger import FakeClock, FakeSerial

    port = tmp_path / "ttyACM0"
    port.touch()
    config = tmp_path / "profile.toml"
    config.write_text(profile_text())
    data = fixture()
    lines = [
        b"[BUILD] version=test channel=testing commit=abcdef0 model=urban "
        b"target=esp32c6 language=en profile=debug\n"
    ]
    if changed:
        lines.append((json.dumps(data) + "\n").encode())
        data["_observations"].update(time_ms=2000, snapshot_seq=2)
    data["_observations"]["settings"]["fetch_wait_ms"] = 101
    lines.append((json.dumps(data) + "\n").encode())
    clock = FakeClock()
    fake = FakeSerial(lines + [b"not read\n"], clock)

    def capture(_serial, artifacts, duration_seconds, **kwargs):
        kwargs.pop("progress_callback", None)
        return capture_raw_serial(
            fake, artifacts, duration_seconds, clock=clock, **kwargs
        )

    patch_cli_capture(monkeypatch, SerialLogStats(lines_read=0, bytes_read=0))
    monkeypatch.setattr("altruist_tester.cli.capture_raw_serial", capture)
    output = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "30s",
            "--config",
            str(config),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 2, result.output
    run = next(output.iterdir())
    assert (run / "serial.log").read_bytes() == b"".join(lines)
    summary = json.loads((run / "summary.json").read_text())
    observations = summary["sensor_observations"]
    assert observations["expected_settings"]["BME280"]["fetch_wait_ms"] == 100
    assert observations["profile_error"]["declared"]["fetch_wait_ms"] == 101
    assert observations["profile_error"]["reason"] == (
        "settings_changed" if changed else "settings_mismatch"
    )
    assert summary["firmware_build"]["last_build_event"]["commit"] == "abcdef0"
    assert "verdict" not in summary
    assert (
        "settings_mismatch" in (run / "report.txt").read_text()
        if not changed
        else "settings_changed" in (run / "report.txt").read_text()
    )
    events = [
        json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()
    ]
    assert events[-1]["reason"] == "observation_settings"


def test_absent_profile_is_not_learned_from_device():
    tracker = SensorObservationTracker(expected_settings={})
    event, samples = feed(tracker, fixture())
    assert event["profile_error"]["expected"] is None
    assert not samples


def test_sparse_failure_tail_preserves_delta_and_reports_lost_times():
    tracker = SensorObservationTracker()
    data = fixture()
    feed(tracker, data)
    meta = data["_observations"]
    meta.update(snapshot_seq=5, time_ms=19000, cycle_seq=19)
    meta["channels"]["main"].update(
        cycles_total=19,
        ok_total=2,
        failed_total=17,
        last_ok_ms=19000,
        last_failed_ms=18000,
        last_recovered_ms=19000,
    )
    meta["failures"] = dict(
        next_seq=18,
        recent=[
            dict(
                seq=i,
                cycle_seq=i + 1,
                at_ms=(i + 1) * 1000,
                channels=["main"],
                reason="read_failed",
            )
            for i in range(2, 18)
        ],
    )
    for item in data["BME280"].values():
        item["observation"].update(seq=2, measured_ms=19000)
    event, samples = feed(tracker, data)
    assert not event.get("invalid_reason") and len(samples) == 3
    assert tracker.coverage == {"failure_tail_eviction": 1}
    assert tracker.missed_snapshots == 3
    assert feed(tracker, data)[1] == []
    assert tracker.coverage == {"failure_tail_eviction": 1}


def test_joint_bme_failure_is_one_record_with_two_channel_counters():
    data = fixture("BME680")
    meta = data["_observations"]
    meta.update(state="error", reason="bus_failed")
    for channel in meta["channels"].values():
        channel.update(
            ok_total=0,
            failed_total=1,
            last_ok_ms=None,
            last_failed_ms=1000,
            state="error",
            reason="bus_failed",
        )
    for item in data["BME680"].values():
        item.update(
            value=None, observation=dict(seq=0, measured_ms=None, state="error")
        )
    meta["failures"] = dict(
        next_seq=2,
        recent=[
            dict(
                seq=1,
                cycle_seq=1,
                at_ms=1000,
                channels=["environment", "gas"],
                reason="bus_failed",
            )
        ],
    )
    event, samples = feed(SensorObservationTracker(), data)
    assert not event.get("invalid_reason") and not samples
    assert len(event["failures"]["recent"]) == 1


def test_changed_metric_without_successful_cycle_fails():
    tracker = SensorObservationTracker()
    data = fixture()
    meta = data["_observations"]
    meta.update(cycle_seq=2)
    meta["channels"]["main"].update(cycles_total=2, ok_total=2)
    feed(tracker, data)
    meta.update(snapshot_seq=2, time_ms=2000)
    data["BME280"]["temperature"]["observation"].update(seq=2, measured_ms=1000)
    assert feed(tracker, data)[0] == {"invalid_reason": "metric_cycle_conflict"}


def test_worst_case_bme_tail_serializes_within_line_limit():
    data = fixture("BME680")
    meta = data["_observations"]
    meta.update(time_ms=2**42 - 1, cycle_seq=17, snapshot_seq=2**53 - 1)
    for channel in meta["channels"].values():
        channel.update(
            cycles_total=17,
            ok_total=1,
            failed_total=16,
            last_ok_ms=2**42 - 1,
            last_failed_ms=2**42 - 2,
            last_recovered_ms=2**42 - 1,
        )
    for item in data["BME680"].values():
        item["observation"]["measured_ms"] = 2**42 - 1
    meta["failures"] = dict(
        next_seq=17,
        recent=[
            dict(
                seq=i,
                cycle_seq=i,
                at_ms=2**42 - 18 + i,
                channels=["environment", "gas"],
                reason="x" * 64,
            )
            for i in range(1, 17)
        ],
    )
    line = json.dumps(data, ensure_ascii=False)
    assert len(line.encode()) < 8192
    assert len(parse_sensor_observation(line)["failures"]["recent"]) == 16


def test_bme_channel_cannot_complete_more_cycles_than_physical_delta():
    tracker = SensorObservationTracker()
    data = fixture("BME680")
    meta = data["_observations"]
    meta["cycle_seq"] = 10
    meta["channels"]["gas"].update(cycles_total=10, ok_total=10)
    assert not feed(tracker, data)[0].get("invalid_reason")
    meta.update(snapshot_seq=2, time_ms=2000, cycle_seq=11)
    meta["channels"]["environment"].update(
        cycles_total=10, ok_total=10, last_ok_ms=2000
    )
    assert feed(tracker, data)[0] == {"invalid_reason": "channel_cycle_delta_conflict"}


def test_two_physical_sensors_cannot_share_one_snapshot():
    data = fixture()
    data["SDS"] = fixture("SDS")["SDS"]
    assert feed(SensorObservationTracker(), data)[0] == {
        "invalid_reason": "multiple_physical_sensors"
    }


def test_init_error_recovers_without_inventing_failed_measurement_cycle():
    tracker = SensorObservationTracker()
    data = fixture()
    meta = data["_observations"]
    meta.update(cycle_seq=0, state="error", reason="init_failed")
    meta["channels"]["main"].update(
        cycles_total=0, ok_total=0, last_ok_ms=None, state="error", reason="init_failed"
    )
    for item in data["BME280"].values():
        item.update(
            value=None, observation=dict(seq=0, measured_ms=None, state="error")
        )
    assert not feed(tracker, data)[0].get("invalid_reason")
    data = fixture()
    meta = data["_observations"]
    meta.update(snapshot_seq=2, time_ms=2000)
    meta["channels"]["main"].update(last_ok_ms=2000, last_recovered_ms=2000)
    for item in data["BME280"].values():
        item["observation"]["measured_ms"] = 2000
    event, samples = feed(tracker, data)
    assert not event.get("invalid_reason") and len(samples) == 3
    assert event["channels"]["main"]["failed_total"] == 0
    assert event["failures"]["recent"] == []


@pytest.mark.parametrize("sensor", ["SCD4x", "BME680", "BMP280"])
@pytest.mark.parametrize("state", ["ok", "error"])
def test_urban_additional_sensors_are_diagnostic_until_selected(sensor, state):
    profile = load_tester_config(Path("configs/urban-activity.example.toml"))
    tracker = SensorObservationTracker(expected_settings=profile.observation_settings)
    data = fixture(sensor)
    if state == "error":
        meta = data["_observations"]
        meta.update(state="error", reason="not_found", cycle_seq=0)
        for channel in meta["channels"].values():
            channel.update(
                cycles_total=0,
                ok_total=0,
                last_ok_ms=None,
                state="error",
                reason="not_found",
            )
        for metric in data[sensor].values():
            metric.update(
                value=None, observation=dict(seq=0, measured_ms=None, state="error")
            )
    event, samples = feed(tracker, data)
    assert event["sensor"] == sensor
    assert tracker.profile_error is None
    assert samples == []
    assert tracker.latest == {}
    assert tracker.as_dict()["sensors"] == []
    assert tracker.as_dict()["acceptance"]["sensors"] == []
    assert tracker.as_dict()["outside_profile"] == {sensor: 1}
    assert tracker.expected_settings == profile.observation_settings

    # Explicit selection restores full validation; UART cannot opt itself in.
    selected = SensorObservationTracker(
        expected_settings={sensor: uart_settings(sensor)}
    )
    assert "profile_error" not in feed(selected, data)[0]
    assert selected.as_dict()["sensors"][0]["sensor"] == sensor
    data["_observations"]["settings"]["fetch_wait_ms"] += 1
    mismatch = SensorObservationTracker(
        expected_settings={sensor: uart_settings(sensor)}
    )
    assert feed(mismatch, data)[0]["profile_error"]["reason"] == "settings_mismatch"
