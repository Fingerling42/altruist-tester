"""Acceptance policy scenarios on synthetic UART v2; no hardware or sleeps."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from altruist_tester.sensor_acceptance import check_observation_cadence
from altruist_tester.sensor_contract import METRICS
from altruist_tester.sensor_observations import (
    SensorObservationTracker,
    observation_findings,
)
from tests.helpers import uart_snapshot

START = datetime(2026, 11, 18, 23, 59, tzinfo=UTC)


def feed(tracker, data, seconds=0):
    event, samples = tracker.feed(json.dumps(data), START + timedelta(seconds=seconds))
    assert not event.get("invalid_reason"), event
    return samples


def slow(sensor="BME280", *, at=1000, seq=1):
    data = uart_snapshot(sensor, {m: 1 for m in METRICS[sensor]})
    meta = data["_observations"]
    meta.update(time_ms=at, snapshot_seq=seq, cycle_seq=seq)
    for c in meta["channels"].values():
        c.update(cycles_total=seq, ok_total=seq, last_ok_ms=at)
    for m in data[sensor].values():
        m["observation"].update(seq=seq, measured_ms=at)
    return data


def results(tracker, seconds=0):
    return observation_findings(
        tracker.as_dict(), reference_time=START + timedelta(seconds=seconds)
    )


def codes(tracker, seconds=0):
    return {(f["code"], f["severity"]) for f in results(tracker, seconds)}


def cadence(tracker, seconds=0):
    return check_observation_cadence(
        tracker.as_dict(), START + timedelta(seconds=seconds)
    )


@pytest.mark.parametrize("sensor", ["BME280", "BMP280", "BME680", "SCD4x", "SDS"])
def test_three_equal_new_identities_and_two_intervals(sensor):
    tracker = SensorObservationTracker(capture_started_at=START)
    for i in range(1, 4):
        feed(tracker, slow(sensor, at=1000 + (i - 1) * 300000, seq=i), (i - 1) * 300)
        assert cadence(tracker, (i - 1) * 300).status == ("ok" if i == 3 else "warn")
    assert all(f.samples_count == 3 for f in cadence(tracker, 600).findings)


@pytest.mark.parametrize(
    "gap,status", [(359.999, "ok"), (360, "warn"), (899.999, "warn"), (900, "fail")]
)
def test_gap_and_tail_inclusive_boundaries_and_recovery(gap, status):
    tracker = SensorObservationTracker(capture_started_at=START)
    for i in range(3):
        feed(tracker, slow(at=1000 + i * 300000, seq=i + 1), i * 300)
    assert cadence(tracker, 600 + gap).status == status
    feed(tracker, slow(at=601000 + int(gap * 1000), seq=4), 600 + gap)
    assert cadence(tracker, 600 + gap).status == status


def unmeasured(data):
    meta = data["_observations"]
    meta.update(cycle_seq=0, state="waiting", reason="waiting")
    for c in meta["channels"].values():
        c.update(
            cycles_total=0,
            ok_total=0,
            last_ok_ms=None,
            state="waiting",
            reason="waiting",
        )
    for m in data[meta["sensor"]].values():
        m.update(value=None, observation=dict(seq=0, measured_ms=None, state="waiting"))
    return data


@pytest.mark.parametrize(
    "seconds,status",
    [(359.999, "warn"), (360, "warn"), (599.999, "warn"), (600, "fail")],
)
def test_first_wait_host_tail_and_late_first_success(seconds, status):
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, unmeasured(slow()))
    assert cadence(tracker, seconds).status == status
    feed(tracker, slow(at=1000 + int(seconds * 1000), seq=2), seconds)
    report = cadence(tracker, seconds)
    assert report.status == status
    assert report.findings[0].samples_count == 1


def test_profile_interval_per_sensor_and_no_cache_count():
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow("SDS")
    data["_observations"]["settings"]["measurement_interval_ms"] = 30000
    feed(tracker, data)
    for i in range(2, 5):
        data["_observations"].update(snapshot_seq=i, time_ms=i * 1000)
        feed(tracker, data, i - 1)
    assert cadence(tracker, 90).findings[0].warn_after_seconds == 90
    assert cadence(tracker, 360).status == "fail"
    assert cadence(tracker, 4).findings[0].samples_count == 1


def test_late_attach_old_cache_is_not_capture_success():
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow(at=1000)
    data["_observations"]["time_ms"] = 301000
    feed(tracker, data)
    assert all(f.samples_count == 0 for f in cadence(tracker).findings)


def fail_cycle(data, *, at, recover=True, channels=None):
    data = copy.deepcopy(data)
    meta = data["_observations"]
    sensor = meta["sensor"]
    names = channels or list(meta["channels"])
    cycle = meta["cycle_seq"] + 1
    meta.update(time_ms=at, snapshot_seq=meta["snapshot_seq"] + 1, cycle_seq=cycle)
    history = meta["failures"]
    history["recent"].append(
        dict(
            seq=history["next_seq"],
            cycle_seq=cycle,
            at_ms=at,
            channels=names,
            reason="read_failed",
        )
    )
    history["next_seq"] += 1
    history["recent"] = history["recent"][-16:]
    for name in names:
        c = meta["channels"][name]
        c.update(
            cycles_total=c["cycles_total"] + 1,
            failed_total=c["failed_total"] + 1,
            last_failed_ms=at,
            state="error",
            reason="read_failed",
        )
    for metric, m in data[sensor].items():
        from altruist_tester.sensor_contract import metric_channel

        if metric_channel(sensor, metric) in names:
            m["observation"]["state"] = "error"
    meta.update(state="error", reason="read_failed")
    if recover:
        meta.update(time_ms=at + 1000, cycle_seq=cycle + 1, state="ok", reason="none")
        for name in names:
            c = meta["channels"][name]
            c.update(
                cycles_total=c["cycles_total"] + 1,
                ok_total=c["ok_total"] + 1,
                last_ok_ms=at + 1000,
                last_recovered_ms=at + 1000,
                state="ok",
                reason="none",
            )
        for metric, m in data[sensor].items():
            from altruist_tester.sensor_contract import metric_channel

            if metric_channel(sensor, metric) in names:
                o = m["observation"]
                o.update(seq=o["seq"] + 1, measured_ms=at + 1000, state="ok")
    return data


@pytest.mark.parametrize(
    "spacing,exceeded", [(86400000 - 1, True), (86400000, True), (86400000 + 1, False)]
)
def test_rolling_day_crosses_midnight_and_retains_fail(spacing, exceeded):
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow()
    feed(tracker, data)
    data = fail_cycle(data, at=61000)
    feed(tracker, data, 61)
    assert ("SENSOR_READ_RECOVERED", "warn") in codes(tracker)
    # Repeated journal/cache snapshots cannot spend the budget twice.
    feed(tracker, data, 61)
    assert ("SENSOR_ERROR_BUDGET", "fail") not in codes(tracker)
    data = fail_cycle(data, at=61000 + spacing)
    feed(tracker, data, 61 + spacing / 1000)
    assert (("SENSOR_ERROR_BUDGET", "fail") in codes(tracker)) == exceeded
    data["_observations"].update(
        snapshot_seq=data["_observations"]["snapshot_seq"] + 1,
        time_ms=61001 + spacing + 86400000,
    )
    feed(tracker, data, 61 + spacing / 1000 + 86400)
    assert (("SENSOR_ERROR_BUDGET", "fail") in codes(tracker)) == exceeded
    state = tracker.as_dict()["acceptance"]["sensors"][0]["channels"]["main"]
    assert state["budget_evidence"] == (
        {"failure_ms": [61000, 61000 + spacing]} if exceeded else None
    )


def test_historical_baseline_and_active_old_failure():
    data = fail_cycle(slow(), at=2000)
    data["_observations"]["time_ms"] = 100000
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, data)
    assert ("SENSOR_ERROR_HISTORY", "warn") in codes(tracker)
    data = fail_cycle(data, at=101000)
    feed(tracker, data, 2)
    assert ("SENSOR_ERROR_BUDGET", "fail") not in codes(tracker)
    assert (
        tracker.as_dict()["acceptance"]["sensors"][0]["channels"]["main"][
            "capture_failed"
        ]
        == 1
    )
    active = fail_cycle(slow(), at=2000, recover=False)
    active["_observations"]["time_ms"] = 100000
    other = SensorObservationTracker(capture_started_at=START)
    feed(other, active)
    assert ("SENSOR_READ_UNRECOVERED", "fail") in codes(other)


def test_start_attribution_uncertain_does_not_claim_zero_errors():
    data = fail_cycle(slow(), at=2000)
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, data, 20)
    assert ("SENSOR_ERROR_COVERAGE", "warn") in codes(tracker)
    assert not any(f["code"] == "SENSOR_ERROR_BUDGET" for f in results(tracker))


def test_gas_and_environment_budgets_independent_and_joint_failure_not_multiplied():
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow("BME680")
    feed(tracker, data)
    data = fail_cycle(data, at=2000, channels=["gas"])
    feed(tracker, data, 2)
    data = fail_cycle(data, at=4000, channels=["environment"])
    feed(tracker, data, 4)
    assert ("SENSOR_ERROR_BUDGET", "fail") not in codes(tracker)
    data = fail_cycle(data, at=6000, channels=["gas", "environment"])
    feed(tracker, data, 6)
    assert sum(f["code"] == "SENSOR_ERROR_BUDGET" for f in results(tracker)) == 2


def audio(*, at, blocks, snapshot_seq=1, windows=None, pause=None, gap=100):
    data = slow("ICS43434", at=at, seq=snapshot_seq)
    meta = data["_observations"]
    pause = pause or dict(
        cycle_seq=0, state="idle", started_ms=None, excluded_ms_total=0
    )
    excluded = pause["excluded_ms_total"]
    active = at - excluded
    windows = windows or []
    complete = active // 300000
    current_blocks = blocks - sum(w["successful_blocks"] for w in windows)
    current_start = windows[-1]["end_ms"] if windows else 0
    meta["channels"]["main"].update(
        cycles_total=blocks, ok_total=blocks, last_ok_ms=at - gap if blocks else None
    )
    meta["cycle_seq"] = blocks
    for m in data["ICS43434"].values():
        m.update(
            value=40 if blocks else None,
            observation=dict(
                seq=blocks,
                measured_ms=at - gap if blocks else None,
                state="ok" if blocks else "waiting",
            ),
        )
    if not blocks:
        meta.update(state="waiting", reason="waiting")
        meta["channels"]["main"].update(state="waiting", reason="waiting")
    meta["noise"] = dict(
        started_ms=0,
        successful_blocks_total=blocks,
        active_ms_total=active,
        last_ok_ms=at - gap if blocks else None,
        sds_pause=pause,
        interval=dict(
            start_ms=0,
            end_ms=at,
            max_gap_active_ms=gap,
            max_gap_end_ms=at,
            open_gap_active_ms=gap,
        ),
        windows=windows,
        window_current=dict(
            seq=complete + 1,
            start_ms=current_start,
            active_ms=active % 300000,
            successful_blocks=current_blocks,
        ),
    )
    return data


@pytest.mark.parametrize(
    "blocks,severity",
    [(600, None), (599, "warn"), (300, "warn"), (299, "fail"), (0, "fail")],
)
def test_noise_full_window_frequency_inclusive_boundaries(blocks, severity):
    tracker = SensorObservationTracker(capture_started_at=START)
    data = audio(at=0, blocks=0, gap=0)
    feed(tracker, data)
    window = dict(
        seq=1, start_ms=0, end_ms=300000, active_ms=300000, successful_blocks=blocks
    )
    data = audio(
        at=300000,
        blocks=blocks,
        snapshot_seq=2,
        windows=[window],
        gap=100 if blocks else 300000,
    )
    feed(tracker, data, 300)
    assert not any(f["code"] == "SENSOR_NOISE_COVERAGE" for f in results(tracker, 300))
    rate = [
        f["severity"] for f in results(tracker, 300) if f["code"] == "SENSOR_NOISE_RATE"
    ]
    assert rate == ([] if severity is None else [severity])


def test_historical_and_incomplete_noise_windows_do_not_pass():
    window = dict(
        seq=1, start_ms=0, end_ms=300000, active_ms=300000, successful_blocks=1500
    )
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=310000, blocks=1550, windows=[window]))
    assert ("SENSOR_NOISE_COVERAGE", "warn") in codes(tracker)


@pytest.mark.parametrize(
    "tail,severity", [(59.9, None), (60, "warn"), (299.9, "warn"), (300, "fail")]
)
def test_noise_host_tail_even_with_live_health(tail, severity):
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=1000, blocks=5, gap=0))
    assert (("SENSOR_NOISE_GAP", severity) in codes(tracker, tail)) == (
        severity is not None
    )


@pytest.mark.parametrize(
    "at,tail,expected",
    [
        (10000, 70, "warn"),
        (10000, 60, None),
        (100000, 60, "warn"),
        (100000, 300, "fail"),
    ],
)
def test_sds_host_tail_excludes_only_remaining_twenty_seconds(at, tail, expected):
    pause = dict(
        cycle_seq=1, state="active", started_ms=0, excluded_ms_total=min(at, 20000)
    )
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=at, blocks=1, pause=pause, gap=0))
    severities = [
        f["severity"] for f in results(tracker, tail) if f["code"] == "SENSOR_NOISE_GAP"
    ]
    assert severities == ([] if expected is None else [expected])


def test_good_average_does_not_hide_long_gap_and_recovery():
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=0, blocks=0, gap=0))
    data = audio(at=299000, blocks=1000, snapshot_seq=2, gap=0)
    data["_observations"]["noise"]["interval"].update(
        max_gap_active_ms=60000, max_gap_end_ms=60000
    )
    feed(tracker, data, 299)
    assert ("SENSOR_NOISE_GAP", "warn") in codes(tracker)
    assert ("SENSOR_NOISE_RATE", "fail") not in codes(tracker)


def test_sparse_slow_updates_warn_instead_of_inventing_failed_cadence():
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, slow())
    feed(tracker, slow(at=1801000, seq=7), 1800)
    feed(tracker, slow(at=2101000, seq=8), 2100)
    report = cadence(tracker, 2100)
    assert report.status == "warn"
    assert all(f.samples_count == 3 for f in report.findings)
    assert all(
        "intermediate measurements not observed" in f.message for f in report.findings
    )


def test_evicted_failure_times_fail_when_delta_proves_breach():
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow()
    feed(tracker, data)
    for i in range(17):
        data = fail_cycle(data, at=3000 + i * 2000)
    feed(tracker, data, 36)
    assert tracker.coverage["failure_tail_eviction"] == 1
    assert ("SENSOR_ERROR_BUDGET", "fail") in codes(tracker)
    assert ("SENSOR_ERROR_COVERAGE", "warn") in codes(tracker)


def test_evicted_failure_times_warn_when_breach_cannot_be_proven():
    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow()
    feed(tracker, data)
    # All retained failures are >24h apart; the missing first timestamp cannot
    # be recovered from counters. The actual unseen first pair was also >24h.
    for i in range(17):
        data = fail_cycle(data, at=1000 + (i + 1) * 86401000)
    feed(tracker, data, 17 * 86401 + 1)
    assert ("SENSOR_ERROR_COVERAGE", "warn") in codes(tracker)
    assert ("SENSOR_ERROR_BUDGET", "fail") not in codes(tracker)


def test_noise_open_gap_cannot_be_reset_without_new_block():
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=1000, blocks=1, gap=100))
    data = audio(at=2000, blocks=1, snapshot_seq=2, gap=100)
    data["_observations"]["channels"]["main"]["last_ok_ms"] = 900
    data["_observations"]["noise"]["last_ok_ms"] = 900
    data["_observations"]["noise"]["interval"]["start_ms"] = 1000
    for m in data["ICS43434"].values():
        m["observation"]["measured_ms"] = 900
    event, _ = tracker.feed(json.dumps(data), START + timedelta(seconds=1))
    # The absolute lower bound already rejects it before temporal validation.
    assert event.get("invalid_reason")


def test_noise_interval_loss_and_missed_windows_preserve_coverage_warning():
    tracker = SensorObservationTracker(capture_started_at=START)
    feed(tracker, audio(at=0, blocks=0, gap=0))
    windows = [
        dict(
            seq=i,
            start_ms=(i - 1) * 300000,
            end_ms=i * 300000,
            active_ms=300000,
            successful_blocks=1500,
        )
        for i in (2, 3)
    ]
    data = audio(at=910000, blocks=4550, snapshot_seq=30, windows=windows)
    # The first completed window fell out of the bounded tail. Keep the
    # current-window blocks independent of the unobserved window's count.
    data["_observations"]["noise"]["window_current"]["successful_blocks"] = 50
    data["_observations"]["noise"]["interval"].update(start_ms=900000)
    feed(tracker, data, 910)
    assert tracker.coverage["noise_interval_loss"] == 1
    assert tracker.coverage["noise_window_loss"] == 1
    assert ("SENSOR_OBSERVATION_COVERAGE", "warn") in codes(tracker)
    assert ("SENSOR_NOISE_RATE", "fail") not in codes(tracker)


def test_noise_pause_across_snapshots_spends_one_budget():
    tracker = SensorObservationTracker(capture_started_at=START)
    pause = dict(cycle_seq=1, state="active", started_ms=1000, excluded_ms_total=0)
    feed(tracker, audio(at=1000, blocks=1, gap=0, pause=pause))
    pause["excluded_ms_total"] = 20000
    data = audio(at=101000, blocks=1, snapshot_seq=2, gap=80000, pause=pause)
    data["_observations"]["channels"]["main"]["last_ok_ms"] = 1000
    data["_observations"]["noise"]["last_ok_ms"] = 1000
    data["_observations"]["noise"]["interval"].update(start_ms=1000)
    for m in data["ICS43434"].values():
        m["observation"]["measured_ms"] = 1000
    feed(tracker, data, 100)
    assert ("SENSOR_NOISE_GAP", "warn") in codes(tracker, 100)
    assert ("SENSOR_NOISE_GAP", "fail") in codes(tracker, 320)


def test_measurement_error_subsystem_uses_v2_recovery_budget_and_preserves_overflow():
    from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
    from altruist_tester.serial_logger import SerialLogStats

    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow()
    feed(tracker, data)
    data = fail_cycle(data, at=2000)
    meta = data["_observations"]
    meta["failures"]["recent"][0]["reason"] = "bmx280_read_failed"
    feed(tracker, data, 2)
    stats = SerialLogStats(
        lines_read=3,
        bytes_read=100,
        sensor_observations=tracker.as_dict(),
        subsystem_event_records=(
            dict(level="error", subsystem="sensor", reason="bmx280_read_failed"),
            dict(level="error", subsystem="sensor", reason="json_overflow"),
        ),
    )
    result = evaluate_rules(stats, RuleEngineConfig())
    subsystem = result.reports.subsystem_health
    assert [(f.reason, f.status) for f in subsystem.findings] == [
        ("bmx280_read_failed", "warn"),
        ("json_overflow", "fail"),
    ]
    assert ("SENSOR_READ_RECOVERED", "warn") in codes(tracker)


def test_long_profile_first_wait_is_not_overruled_by_global_startup_window():
    from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
    from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries
    from altruist_tester.serial_logger import SerialLogStats

    tracker = SensorObservationTracker(capture_started_at=START)
    data = slow(at=1000)
    data["_observations"]["settings"]["measurement_interval_ms"] = 900000
    feed(tracker, data, 900)
    samples = SensorSampleSeries()
    samples.append(
        SensorSampleRecord(
            ts=(START + timedelta(seconds=900)).isoformat(),
            sensor="BME280",
            metric="temperature",
            value=24,
            unit="C",
            source="serial_observation_v2",
        )
    )
    stats = SerialLogStats(
        lines_read=1,
        bytes_read=100,
        capture_started_at=START.isoformat(),
        sensor_series=samples,
        sensor_samples_count=1,
        sensor_observations=tracker.as_dict(),
    )
    result = evaluate_rules(
        stats, RuleEngineConfig(reference_time=START + timedelta(seconds=900))
    )
    assert result.reports.sensor_cadence.status == "warn"
    assert not any(
        f.code == "LOG_CONTRACT_SENSOR_SAMPLES_LATE" for f in result.findings
    )
