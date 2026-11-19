"""Explicit acquisition-only acceptance must never create sensor measurements."""

import copy
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from altruist_tester.config import ConfigError, load_tester_config
from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
from altruist_tester.sensor_acceptance import check_observation_cadence
from altruist_tester.sensor_contract import METRICS, metric_channel, parse_contract
from altruist_tester.sensor_observations import (
    SensorObservationTracker,
    observation_findings,
)
from altruist_tester.serial_logger import SerialLogStats
from tests.helpers import sample_record, sample_series, uart_snapshot
from tests.test_sensor_acceptance import START, audio


def limited(
    sensor="ICS43434",
    *,
    at=1000,
    seq=1,
    attempts=0,
    activity=0,
    result="never",
    previous=0,
):
    channel = "gas" if sensor == "BME680" else "main"
    if sensor == "ICS43434":
        data = audio(at=at, blocks=0, snapshot_seq=seq, gap=at)
        data["_observations"]["noise"]["interval"]["start_ms"] = previous
    else:
        data = uart_snapshot(
            sensor, {m: 24 for m in ("temperature", "humidity", "pressure")}
        )
        meta = data["_observations"]
        meta.update(time_ms=at, snapshot_seq=seq, cycle_seq=seq)
        meta["channels"]["environment"].update(
            cycles_total=seq, ok_total=seq, last_ok_ms=at
        )
        for name, item in data[sensor].items():
            if metric_channel(sensor, name) == "environment":
                item["observation"].update(seq=seq, measured_ms=at)
    meta = data["_observations"]
    meta.update(state="waiting", reason="driver_unverified")
    meta["channels"][channel].update(
        cycles_total=0,
        ok_total=0,
        failed_total=0,
        last_ok_ms=None,
        last_failed_ms=None,
        last_recovered_ms=None,
        state="waiting",
        reason="driver_unverified",
    )
    for name, item in data[sensor].items():
        if metric_channel(sensor, name) == channel:
            item.update(
                value=None, observation=dict(seq=0, measured_ms=None, state="waiting")
            )
    transferring = result in {"activity", "no_activity"}
    p = dict(
        attempts_total=attempts,
        transfers_total=attempts if transferring else 0,
        activity_total=activity,
        last_attempt_ms=at if attempts else None,
        last_transfer_ms=at if transferring else None,
        last_activity_ms=at if activity else None,
        result=result,
        error_code=0,
        bytes_read=8192 if transferring and sensor == "ICS43434" else 0,
        expected_bytes=8192 if sensor == "ICS43434" else 0,
        signal_min=-10 if transferring and sensor == "ICS43434" else None,
        signal_max=10 if transferring and sensor == "ICS43434" else None,
        gas_status=0 if transferring and sensor == "BME680" else None,
        gas_resistance=50000 if transferring and sensor == "BME680" else None,
    )
    meta["probes"] = {channel: p}
    return data


def feed(t, data):
    at = data["_observations"]["time_ms"]
    result, samples = t.feed(json.dumps(data), START + timedelta(milliseconds=at))
    assert not t.issues, result
    return samples


def findings(t, tail=0):
    latest = max(s["time_ms"] for s in t.as_dict()["sensors"])
    return observation_findings(
        t.as_dict(),
        reference_time=START + timedelta(milliseconds=latest) + timedelta(seconds=tail),
    )


@pytest.mark.parametrize(
    "sensor,pair", [("BME680", "BME680.gas"), ("ICS43434", "ICS43434.main")]
)
def test_explicit_activity_mode_accepts_fresh_evidence_with_warn(sensor, pair):
    t = SensorObservationTracker(activity_only=(pair,))
    feed(t, limited(sensor))
    samples = feed(
        t,
        limited(
            sensor,
            at=2000,
            seq=2,
            attempts=1,
            activity=1,
            result="activity",
            previous=1000,
        ),
    )
    assert all(
        metric_channel(s.sensor, s.metric) != pair.split(".")[1] for s in samples
    )
    f = findings(t)
    assert not any(x["severity"] == "fail" for x in f), f
    assert any(
        x["code"] == "SENSOR_ACTIVITY_OBSERVED" and x["severity"] == "warn" for x in f
    )
    assert len(check_observation_cadence(t.as_dict(), None).findings) == (
        3 if sensor == "BME680" else 0
    )


def test_full_acceptance_rejects_activity_only_firmware():
    t = SensorObservationTracker()
    feed(t, limited())
    assert any(
        x["code"] == "SENSOR_ACTIVITY_ONLY_NOT_ENABLED" and x["severity"] == "fail"
        for x in findings(t)
    )


def test_historical_activity_and_uart_duplicates_do_not_establish_presence():
    t = SensorObservationTracker(activity_only=("ICS43434.main",))
    data = limited(attempts=1, activity=1, result="activity")
    feed(t, data)
    feed(t, data)
    assert any(x["code"] == "SENSOR_ACTIVITY_UNCONFIRMED" for x in findings(t))


@pytest.mark.parametrize(
    "result", ["init_failed", "read_failed", "short_read", "no_activity", "no_new_data"]
)
def test_failed_or_inactive_acquisition_fails(result):
    t = SensorObservationTracker(activity_only=("ICS43434.main",))
    feed(t, limited())
    feed(t, limited(at=2000, seq=2, attempts=1, result=result, previous=1000))
    assert any(
        x["code"] == "SENSOR_ACTIVITY_UNCONFIRMED" and x["severity"] == "fail"
        for x in findings(t)
    )


def test_missing_probe_is_failure_even_without_any_snapshot():
    t = SensorObservationTracker(activity_only=("BME680.gas",))
    data = uart_snapshot("BME280", {m: 24 for m in METRICS["BME280"]})
    feed(t, data)
    assert any(x["code"] == "SENSOR_ACTIVITY_MISSING" for x in findings(t))


def test_activity_host_tail_does_not_depend_on_live_health():
    t = SensorObservationTracker(activity_only=("ICS43434.main",))
    feed(t, limited())
    feed(
        t,
        limited(
            at=2000, seq=2, attempts=1, activity=1, result="activity", previous=1000
        ),
    )
    assert any(
        x["code"] == "SENSOR_ACTIVITY_UNCONFIRMED" for x in findings(t, tail=300)
    )


@pytest.mark.parametrize("status", [0, 0x10, 0x20, 0x30])
def test_gas_flags_are_evidence_and_never_fresh_voc_measurements(status):
    data = limited("BME680", attempts=1, activity=1, result="activity")
    data["_observations"]["probes"]["gas"]["gas_status"] = status
    parsed = parse_contract(json.dumps(data))
    assert parsed["probes"]["gas"]["gas_status"] == status
    assert all(
        m["seq"] == 0 and m["value"] is None
        for m in parsed["metrics"]
        if metric_channel("BME680", m["metric"]) == "gas"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("activity_total", True),
        ("activity_total", 2),
        ("result", []),
        ("bytes_read", 9000),
        ("signal_min", 100),
        ("last_activity_ms", None),
    ],
)
def test_bad_probe_evidence_is_atomic_contract_failure(key, value):
    data = limited(attempts=1, activity=1, result="activity")
    data["_observations"]["probes"]["main"][key] = value
    with pytest.raises(ValueError):
        parse_contract(json.dumps(data))


def test_forged_measurement_success_in_probe_channel_is_rejected():
    data = limited(attempts=1, activity=1, result="activity")
    data["_observations"]["channels"]["main"].update(
        cycles_total=1, ok_total=1, last_ok_ms=1000, state="ok", reason="none"
    )
    data["_observations"].update(cycle_seq=1, state="ok", reason="none")
    with pytest.raises(ValueError, match="probe_measurement_conflict"):
        parse_contract(json.dumps(data))


def test_probe_counters_cannot_regress_or_change_without_attempt():
    t = SensorObservationTracker(activity_only=("ICS43434.main",))
    first = limited(attempts=1, activity=1, result="activity")
    feed(t, first)
    second = copy.deepcopy(first)
    second["_observations"].update(time_ms=2000, snapshot_seq=2)
    second["_observations"]["noise"].update(active_ms_total=2000)
    second["_observations"]["noise"]["window_current"]["active_ms"] = 2000
    second["_observations"]["noise"]["interval"].update(
        start_ms=1000,
        end_ms=2000,
        max_gap_active_ms=2000,
        max_gap_end_ms=2000,
        open_gap_active_ms=2000,
    )
    second["_observations"]["probes"]["main"]["signal_max"] = 11
    t.feed(json.dumps(second), START + timedelta(seconds=2))
    assert t.issues["probe_changed_without_attempt"] == 1


def test_engine_keeps_unrelated_presence_checks_and_never_passes_limited_mode():
    t = SensorObservationTracker(activity_only=("ICS43434.main",))
    feed(t, limited())
    feed(
        t,
        limited(
            at=2000, seq=2, attempts=1, activity=1, result="activity", previous=1000
        ),
    )
    stats = SerialLogStats(
        lines_read=2,
        bytes_read=200,
        sensor_observations=t.as_dict(),
        sensor_series=sample_series(sample_record("BME280", "temperature", 24, 0)),
    )
    result = evaluate_rules(
        stats,
        RuleEngineConfig(
            expected_sensors=("ics-43434", "bme280"), expected_metrics=("noise_avg",)
        ),
    )
    assert result.reports.sensor_presence.missing_sensor_metrics == (
        ("bme280", "humidity"),
        ("bme280", "pressure"),
    )
    assert result.verdict == "FAIL"
    assert any(f.code == "SENSOR_ACTIVITY_ONLY" for f in result.findings)


@pytest.mark.parametrize(
    "value", ['["SDS.main"]', '["BME680.gas", "BME680.gas"]', '["ICS43434.main"]']
)
def test_mode_requires_known_unique_channels_and_explicit_settings(tmp_path, value):
    config = tmp_path / "profile.toml"
    config.write_text("[observations]\nactivity_only = " + value + "\n")
    with pytest.raises(ConfigError, match="activity_only"):
        load_tester_config(config)


@pytest.mark.parametrize(
    "model,pair", [("urban", "ICS43434.main"), ("insight", "BME680.gas")]
)
def test_activity_example_profiles_are_explicit(model, pair):
    from pathlib import Path

    profile = Path(__file__).parents[1] / "configs" / f"{model}-activity.example.toml"
    config = load_tester_config(profile)
    assert config.observation_activity_only == (pair,)
    full = load_tester_config(profile.with_name(f"{model}.example.toml"))
    assert full.observation_activity_only == ()
    assert config.observation_settings == full.observation_settings


@pytest.mark.parametrize(
    "active,expected_exit,verdict", [(True, 0, "WARN"), (False, 1, "FAIL")]
)
def test_cli_records_explicit_mode_evidence_and_exit_code(
    monkeypatch, tmp_path, active, expected_exit, verdict
):
    from typer.testing import CliRunner

    from altruist_tester.cli import app
    from tests.cli_helpers import patch_cli_capture, release_log_stats

    profile = tmp_path / "profile.toml"
    profile.write_text("""[expect]
metrics = ["temperature"]
[observations]
activity_only = ["ICS43434.main"]
firmware_source = "debug UART v2"
settings_rule = "explicit test settings"
[observations.settings.ICS43434]
fetch_wait_ms = 100
publication = "coalesced"
publish_interval_ms = 30000
state_min_interval_ms = 1000
sds_warmup_ms = 15000
sds_collect_ms = 5000
noise_window_active_ms = 300000
""")
    tracker = SensorObservationTracker(activity_only=("ICS43434.main",))
    feed(tracker, limited())
    feed(
        tracker,
        limited(
            at=2000,
            seq=2,
            attempts=1,
            activity=int(active),
            result="activity" if active else "no_activity",
            previous=1000,
        ),
    )
    stats = replace(release_log_stats(), sensor_observations=tracker.as_dict())
    forwarded = {}

    def hook(_port, _artifacts, _duration, **kwargs):
        forwarded.update(kwargs)

    patch_cli_capture(monkeypatch, stats, capture_hook=hook)
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])
    monkeypatch.setattr(
        "altruist_tester.cli.utc_now", lambda: START + timedelta(seconds=2)
    )
    port = tmp_path / "ttyACM0"
    port.touch()
    output = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "2s",
            "--config",
            str(profile),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == expected_exit, result.output
    assert forwarded["observation_activity_only"] == ("ICS43434.main",)
    run = next(output.iterdir())
    summary = json.loads((run / "summary.json").read_text())
    assert summary["verdict"] == verdict
    assert summary["sensor_observations"]["activity_only"] == ["ICS43434.main"]
    assert summary["sensor_observations"]["probe_baselines"] == {"ICS43434.main": 0}
    report = (run / "report.txt").read_text()
    assert "activity_only" in report and "measurement validity" in report
