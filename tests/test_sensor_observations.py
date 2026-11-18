import json
from datetime import UTC, datetime, timedelta

import pytest

from altruist_tester.parsers.sensor_values import parse_sensor_values
from altruist_tester.sensor_observations import (
    SensorObservationTracker,
    observation_findings,
    parse_sensor_observation,
)

NOW = datetime(2026, 11, 18, tzinfo=UTC)


def snapshot(
    *,
    seq=1,
    measured=1000,
    time=1000,
    snapshot_seq=1,
    state="ok",
    boot="a-b",
    value=24.0,
):
    return {
        "_observations": {
            "version": 1,
            "device_id": "1051DB010C70",
            "boot_id": boot,
            "model": "urban",
            "sensor": "BME280",
            "snapshot_seq": snapshot_seq,
            "time_ms": time,
            "state": "ok",
            "errors": 0,
            "reason": "none",
        },
        "BME280": {
            "temperature": {
                "value": value,
                "units": "°C",
                "observation": {"seq": seq, "measured_ms": measured, "state": state},
            }
        },
    }


def feed(tracker, **kwargs):
    return tracker.feed(json.dumps(snapshot(**kwargs)), NOW)


def test_repeated_snapshot_and_new_equal_value_are_distinct():
    tracker = SensorObservationTracker()
    assert len(feed(tracker)[1]) == 1
    assert feed(tracker)[1] == []
    assert feed(tracker, time=2000, snapshot_seq=2)[1] == []
    assert len(feed(tracker, seq=2, measured=3000, time=3000, snapshot_seq=3)[1]) == 1
    assert tracker.duplicates == 2


def test_error_with_cache_does_not_refresh_measurement():
    tracker = SensorObservationTracker()
    feed(tracker)
    data = snapshot(time=5000, snapshot_seq=2, state="error")
    data["_observations"].update(state="error", errors=1, reason="bmx280_read_failed")
    assert tracker.feed(json.dumps(data), NOW)[1] == []
    assert tracker.latest[("BME280", "temperature")]["measured_ms"] == 1000
    findings = observation_findings(
        tracker.as_dict(), reference_time=NOW, interval=300, warn_after=2, fail_after=4
    )
    assert any(f["severity"] == "fail" for f in findings)


def test_repeated_old_value_is_stale_even_at_late_attach():
    tracker = SensorObservationTracker()
    _, samples = feed(tracker, time=1301000)
    assert (
        samples[0].observation["measured_ts"]
        == (NOW - timedelta(seconds=1300)).isoformat()
    )
    findings = observation_findings(
        tracker.as_dict(), reference_time=NOW, interval=300, warn_after=2, fail_after=4
    )
    assert any(f["severity"] == "fail" for f in findings)


def test_reboot_changes_measurement_identity_and_is_reported():
    tracker = SensorObservationTracker()
    feed(tracker, seq=10)
    assert len(feed(tracker, boot="c-d")[1]) == 1
    assert tracker.issues["reboot"] == 1
    assert feed(tracker, boot="a-b")[1] == []
    assert tracker.issues["retired_boot"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["_observations"].update(version=2),
        lambda d: d["_observations"].update(version=True),
        lambda d: d["_observations"].update(time_ms=-1),
        lambda d: d["BME280"]["temperature"].pop("observation"),
        lambda d: d["BME280"]["temperature"]["observation"].update(measured_ms=1001),
        lambda d: d["BME280"]["temperature"].update(value=float("nan")),
        lambda d: d["BME280"]["temperature"].update(value=True),
    ],
)
def test_invalid_contract_never_falls_back_to_legacy(mutation):
    data = snapshot()
    mutation(data)
    line = json.dumps(data)
    tracker = SensorObservationTracker()
    observation, samples = tracker.feed(line, NOW)
    assert "invalid_reason" in observation
    assert samples == []
    assert tracker.selected
    assert parse_sensor_values(line) == []


def test_truncated_record_does_not_salvage_nested_values():
    tracker = SensorObservationTracker()
    observation, samples = tracker.feed(json.dumps(snapshot())[:-1], NOW)
    assert observation == {"invalid_reason": "invalid_json"}
    assert not samples


def test_missing_metric_and_changed_value_with_same_identity_fail():
    tracker = SensorObservationTracker()
    feed(tracker)
    observation, samples = feed(tracker, snapshot_seq=2, time=2000, value=25)
    assert observation == {"invalid_reason": "measurement_regression"}
    assert not samples
    data = snapshot(snapshot_seq=3, time=3000)
    data["BME280"] = {}
    tracker.feed(json.dumps(data), NOW)
    assert tracker.issues["missing_metric"] == 1


def test_64_bit_clock_and_snapshot_loss_are_observable():
    tracker = SensorObservationTracker()
    feed(tracker, measured=2**32 - 10, time=2**32)
    _, samples = feed(
        tracker, seq=3, measured=2**32 + 1000, time=2**32 + 1001, snapshot_seq=4
    )
    assert len(samples) == 1
    assert tracker.missed_snapshots == 2
    assert tracker.unobserved_updates == 1


def test_physical_sources_do_not_merge():
    tracker = SensorObservationTracker()
    feed(tracker)
    data = snapshot()
    data["SCD4x"] = data.pop("BME280")
    data["_observations"]["sensor"] = "SCD4x"
    _, samples = tracker.feed(json.dumps(data), NOW)
    assert samples[0].sensor == "SCD4x"
    assert len(tracker.latest) == 2


def test_freshness_uses_host_tail_since_last_snapshot():
    tracker = SensorObservationTracker()
    feed(tracker)
    findings = observation_findings(
        tracker.as_dict(),
        reference_time=NOW + timedelta(seconds=1200),
        interval=300,
        warn_after=2,
        fail_after=4,
    )
    assert any(f["severity"] == "fail" for f in findings)


def test_unknown_extensions_do_not_change_schema():
    data = snapshot()
    data["_observations"]["extension"] = {"anything": "ignored"}
    assert parse_sensor_observation(json.dumps(data))["metrics"][0]["value"] == 24


@pytest.mark.parametrize("field", ["time_ms", "snapshot_seq"])
def test_decimal_wire_clocks(field):
    data = snapshot()
    data["_observations"][field] = "4294967297"
    data["BME280"]["temperature"]["observation"].update(seq="1", measured_ms="1000")
    parsed = parse_sensor_observation(json.dumps(data))
    assert parsed[field] == 4294967297
    assert parsed["metrics"][0]["measured_ms"] == 1000


def test_valid_new_json_remains_available_to_stateless_diagnostics():
    samples = parse_sensor_values(json.dumps(snapshot()))
    assert samples[0].source == "serial_observation_v1"
    assert samples[0].sensor == "BME280"


@pytest.mark.parametrize(
    "field,value", [("state", []), ("model", {}), ("time_ms", 2**53)]
)
def test_malformed_types_never_crash_capture(field, value):
    data = snapshot()
    data["_observations"][field] = value
    tracker = SensorObservationTracker()
    assert tracker.feed(json.dumps(data), NOW)[0].get("invalid_reason")


def test_capture_selects_independent_source_and_preserves_raw_uart(tmp_path):
    from altruist_tester.serial_logger import capture_raw_serial
    from tests.test_serial_logger import (
        FakeClock,
        FakeSerial,
        _create_artifacts,
        _read_jsonl,
    )

    legacy = json.dumps({"BME280": {"temperature": {"value": 99}}})
    lines = [
        legacy,
        json.dumps(snapshot()),
        json.dumps(snapshot(time=2000, snapshot_seq=2)),
        (
            "[PAYLOAD] channel=sensors-connectivity encoding=plain encrypted=0 "
            "sample_available=1 sample=t:77 payload_len=4"
        ),
        json.dumps(snapshot(seq=2, measured=3000, time=3000, snapshot_seq=3)),
        legacy,
    ]
    raw = [(line + "\n").encode() for line in lines]
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path, duration_input="2s", duration_seconds=2)
    stats = capture_raw_serial(FakeSerial(raw, clock), artifacts, 2, clock=clock)
    assert artifacts.serial_log.read_bytes() == b"".join(raw)
    assert stats.sensor_samples_count == 2
    assert [r.value for r in stats.sensor_series.by_key[("BME280", "temperature")]] == [
        24,
        24,
    ]
    assert stats.sensor_observations["duplicates"] == 1
    # Non-versioned values never enter measurement artifacts.
    records = _read_jsonl(artifacts.samples_jsonl)
    assert len(records) == 2
    assert records[-1]["observation"]["seq"] == 2
    assert any(
        e["type"] == "sensor_observation" for e in _read_jsonl(artifacts.events_jsonl)
    )


def test_measurement_time_drives_cadence_and_flatline():
    from altruist_tester.rules.cadence import check_series_cadence
    from altruist_tester.rules.flatline import check_series_flatline
    from altruist_tester.samples import SensorSampleRecord

    records = [
        SensorSampleRecord(
            ts=NOW.isoformat(),
            sensor="BME280",
            metric="temperature",
            value=24,
            unit="°C",
            source="serial_observation_v1",
            observation={"measured_ts": (NOW + timedelta(seconds=offset)).isoformat()},
        )
        for offset in (0, 1200)
    ]
    assert check_series_cadence("BME280", "temperature", records).status == "fail"
    assert (
        check_series_flatline(
            "BME280",
            "temperature",
            records,
            flatline_window_seconds=300,
            flatline_fail_after_seconds=600,
        ).status
        == "fail"
    )


def test_engine_includes_contract_failure_in_verdict():
    from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
    from altruist_tester.serial_logger import SerialLogStats

    stats = SerialLogStats(
        lines_read=1,
        bytes_read=1,
        sensor_observations={"selected": True, "issues": {"unknown_version": 1}},
    )
    result = evaluate_rules(stats, RuleEngineConfig())
    assert result.verdict == "FAIL"
    assert any(f.code == "SENSOR_OBSERVATION_UNKNOWN_VERSION" for f in result.findings)


def test_unknown_metric_observation_fields_cannot_override_validated_values():
    data = snapshot()
    data["BME280"]["temperature"]["observation"].update(
        metric="pressure", value=-100, unit="fake"
    )
    samples = SensorObservationTracker().feed(json.dumps(data), NOW)[1]
    assert samples[0].metric == "temperature"
    assert samples[0].value == 24
    assert samples[0].unit == "°C"


def test_extremely_large_number_is_rejected_without_overflow():
    data = snapshot(value=10**400)
    observation, samples = SensorObservationTracker().feed(json.dumps(data), NOW)
    assert observation == {"invalid_reason": "invalid_value"}
    assert not samples


@pytest.mark.parametrize(
    "transport",
    [
        (
            "[PAYLOAD] channel=datalog encoding=plain encrypted=0 "
            "sample_available=1 payload_len=4 sample=t:99"
        ),
        (
            "[PAYLOAD] channel=datalog encoding=cps encrypted=1 "
            "sample_available=0 payload_len=100"
        ),
        "[PROTO] HTTP code=200",
        (
            "[PAYLOAD] channel=sensors-connectivity encoding=plain encrypted=0 "
            "sample_available=1 payload_len=4 sample=t:99"
        ),
    ],
)
def test_independent_source_does_not_depend_on_transport(tmp_path, transport):
    from altruist_tester.serial_logger import capture_raw_serial
    from tests.test_serial_logger import FakeClock, FakeSerial, _create_artifacts

    lines = [
        (json.dumps(snapshot()) + "\n").encode(),
        (transport + "\n").encode(),
        (
            json.dumps(snapshot(seq=2, measured=2000, time=2000, snapshot_seq=2)) + "\n"
        ).encode(),
    ]
    clock = FakeClock()
    stats = capture_raw_serial(
        FakeSerial(lines, clock), _create_artifacts(tmp_path), 1, clock=clock
    )
    assert stats.sensor_samples_count == 2
    assert all(
        record.source == "serial_observation_v1"
        for record in stats.sensor_series.by_key[("BME280", "temperature")]
    )
    assert not stats.sensor_observations["issues"]


def test_capture_checks_identity_from_observation_metadata(tmp_path):
    from altruist_tester.serial_logger import SerialIdentityError, capture_raw_serial
    from tests.test_serial_logger import FakeClock, FakeSerial, _create_artifacts

    clock = FakeClock()
    with pytest.raises(SerialIdentityError):
        capture_raw_serial(
            FakeSerial([(json.dumps(snapshot()) + "\n").encode()], clock),
            _create_artifacts(tmp_path),
            1,
            clock=clock,
            expected_device_id="588C8140B8EC",
        )


def test_missing_versioned_observations_fail_even_with_plain_sensor_json(tmp_path):
    from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
    from altruist_tester.serial_logger import capture_raw_serial
    from tests.test_serial_logger import FakeClock, FakeSerial, _create_artifacts

    raw = b'{"BME280":{"temperature":{"value":24}}}\n'
    clock = FakeClock()
    stats = capture_raw_serial(
        FakeSerial([raw], clock), _create_artifacts(tmp_path), 1, clock=clock
    )
    assert stats.sensor_samples_count == 0
    assert stats.sensor_observations["source"] == "serial_observation_v1"
    result = evaluate_rules(stats, RuleEngineConfig())
    assert result.verdict == "FAIL"
    assert any(f.code == "SENSOR_OBSERVATION_MISSING" for f in result.findings)
