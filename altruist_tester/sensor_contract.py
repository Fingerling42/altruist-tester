"""Atomic validation of the debug UART v2 wire contract."""

from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_CLOCK = 2**42 - 1
MAX_COUNTER = 2**53 - 1
STATES = {"never", "warming", "waiting", "ok", "error"}
METRICS = {
    "BME280": {"temperature", "humidity", "pressure"},
    "BMP280": {"temperature", "pressure"},
    "BME680": {
        "temperature",
        "humidity",
        "pressure",
        "gas_resistance",
        "gas_baseline",
        "voc_ppb",
        "voc_index",
        "voc_level",
        "voc_spikes_today",
    },
    "SCD4x": {"temperature", "humidity", "co2"},
    "SDS": {"P1", "P2"},
    "ICS43434": {"noiseAvg", "noiseMax"},
}
SETTINGS = (
    "fetch_wait_ms",
    "measurement_interval_ms",
    "publication",
    "publish_interval_ms",
    "state_min_interval_ms",
    "gas_enabled",
    "sds_warmup_ms",
    "sds_collect_ms",
    "noise_window_active_ms",
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def obj(value: Any, reason: str) -> dict:
    require(isinstance(value, dict), reason)
    return value


def number(value: Any, *, maximum: int = MAX_COUNTER, minimum: int = 0) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}", value):
        value = int(value)
    require(type(value) is int and minimum <= value <= maximum, "invalid_integer")
    return value


def clock(value: Any, now: int, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    return number(value, maximum=min(now, MAX_CLOCK))


def state_reason(value: dict) -> dict:
    state, reason = value.get("state"), value.get("reason")
    require(isinstance(state, str) and state in STATES, "invalid_state")
    require(
        isinstance(reason, str) and bool(re.fullmatch(r"[a-z0-9_]{1,64}", reason)),
        "invalid_reason",
    )
    require(
        (state != "ok" or reason == "none") and (state != "error" or reason != "none"),
        "state_reason_conflict",
    )
    return {"state": state, "reason": reason}


def metric_channel(sensor: str, metric: str) -> str:
    if sensor != "BME680":
        return "main"
    return "environment" if metric in {"temperature", "humidity", "pressure"} else "gas"


def validate_settings(sensor: str, value: Any) -> dict:
    value = obj(value, "invalid_settings")
    require(all(k in value for k in SETTINGS), "missing_settings")
    result = {}
    for key in SETTINGS:
        v = value[key]
        if key == "publication":
            require(
                v == ("coalesced" if sensor == "ICS43434" else "on_update"),
                "invalid_publication",
            )
        elif key == "gas_enabled":
            require(
                type(v) is bool if sensor == "BME680" else v is None,
                "invalid_gas_enabled",
            )
        else:
            nullable = (
                (key == "measurement_interval_ms" and sensor == "ICS43434")
                or (
                    key in {"sds_warmup_ms", "sds_collect_ms"}
                    and sensor not in {"SDS", "ICS43434"}
                )
                or (key == "noise_window_active_ms" and sensor != "ICS43434")
            )
            if nullable:
                require(v is None, "invalid_settings_null")
            else:
                v = number(v, maximum=MAX_CLOCK)
        result[key] = v
    require(result["fetch_wait_ms"] > 0, "invalid_fetch_wait")
    require(
        result["publish_interval_ms"] == (30000 if sensor == "ICS43434" else 0),
        "invalid_publish_interval",
    )
    require(result["state_min_interval_ms"] == 1000, "invalid_state_interval")
    if sensor != "ICS43434":
        require(
            result["measurement_interval_ms"] >= (30000 if sensor == "SDS" else 300000),
            "invalid_measurement_interval",
        )
    if sensor in {"SDS", "ICS43434"}:
        pair = (result["sds_warmup_ms"], result["sds_collect_ms"])
        require(
            pair
            in ({(15000, 5000), (0, 0)} if sensor == "ICS43434" else {(15000, 5000)}),
            "invalid_sds_timing",
        )
    if sensor == "ICS43434":
        require(result["noise_window_active_ms"] == 300000, "invalid_noise_window")
    return result


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse_contract(line: str) -> dict | None:
    if '"_observations"' not in line:
        return None
    require(len(line.encode("utf-8")) <= 8192, "line_too_long")
    try:
        data = json.loads(line[line.index("{") :], object_pairs_hook=_unique_object)
    except (TypeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    meta = obj(obj(data, "invalid_json").get("_observations"), "missing_version")
    require(
        type(meta.get("version")) is int and meta["version"] == 2, "unknown_version"
    )
    require(meta.get("error") != "snapshot_overflow", "snapshot_overflow")
    require("error" not in meta, "invalid_snapshot_error")
    for key, pattern in (
        ("device_id", r"[0-9a-fA-F]{12}"),
        ("boot_id", r"[0-9a-fA-F]{1,8}-[0-9a-fA-F]{1,8}"),
    ):
        require(
            isinstance(meta.get(key), str) and bool(re.fullmatch(pattern, meta[key])),
            "invalid_" + key,
        )
    require(
        isinstance(meta.get("model"), str) and meta["model"] in {"urban", "insight"},
        "invalid_model",
    )
    sensor = meta.get("sensor")
    require(isinstance(sensor, str) and sensor in METRICS, "invalid_sensor")
    require("errors" not in meta, "obsolete_errors")
    require(not (set(data) & (METRICS.keys() - {sensor})), "multiple_physical_sensors")
    now = number(meta.get("time_ms"), maximum=MAX_CLOCK)
    result = {k: meta[k] for k in ("device_id", "boot_id", "model", "sensor")}
    result.update(
        version=2,
        time_ms=now,
        snapshot_seq=number(meta.get("snapshot_seq"), minimum=1),
        cycle_seq=number(meta.get("cycle_seq")),
        **state_reason(meta),
    )
    result["settings"] = settings = validate_settings(sensor, meta.get("settings"))
    channels = obj(meta.get("channels"), "missing_channels")
    expected = {"environment", "gas"} if sensor == "BME680" else {"main"}
    require(expected <= channels.keys(), "missing_channel")
    parsed_channels = {}
    for name in sorted(expected):
        channel = obj(channels[name], "invalid_channel")
        parsed = {
            k: number(channel.get(k))
            for k in ("cycles_total", "ok_total", "failed_total")
        }
        require(
            parsed["cycles_total"]
            == parsed["ok_total"] + parsed["failed_total"]
            <= result["cycle_seq"],
            "cycle_counter_conflict",
        )
        parsed.update(state_reason(channel))
        for k in ("last_ok_ms", "last_failed_ms", "last_recovered_ms"):
            require(k in channel, "missing_channel_time")
            parsed[k] = clock(channel[k], now, nullable=True)
        require(
            (parsed["ok_total"] == 0) == (parsed["last_ok_ms"] is None),
            "ok_time_conflict",
        )
        require(
            (parsed["failed_total"] == 0) == (parsed["last_failed_ms"] is None),
            "failed_time_conflict",
        )
        recovered = parsed["last_recovered_ms"]
        require(
            recovered is None
            or (parsed["last_ok_ms"] is not None and recovered <= parsed["last_ok_ms"]),
            "recovery_time_conflict",
        )
        if parsed["state"] == "ok":
            require(
                parsed["ok_total"] > 0
                and (
                    parsed["last_failed_ms"] is None
                    or parsed["last_ok_ms"] >= parsed["last_failed_ms"]
                ),
                "channel_state_conflict",
            )
        if parsed["last_failed_ms"] is not None and (
            parsed["last_ok_ms"] is None
            or parsed["last_failed_ms"] > parsed["last_ok_ms"]
        ):
            require(parsed["state"] == "error", "unrecovered_channel")
        if parsed["state"] == "never":
            require(parsed["cycles_total"] == 0, "channel_state_conflict")
        parsed_channels[name] = parsed
    result["channels"] = parsed_channels
    require(
        result["cycle_seq"] <= sum(c["cycles_total"] for c in parsed_channels.values()),
        "cycle_counter_conflict",
    )
    require(
        (result["state"] == "error")
        == any(c["state"] == "error" for c in parsed_channels.values()),
        "sensor_state_conflict",
    )
    if result["state"] == "never":
        require(result["cycle_seq"] == 0, "sensor_state_conflict")
    if result["state"] == "ok":
        require(
            all(c["state"] == "ok" for c in parsed_channels.values())
            if settings["gas_enabled"]
            else parsed_channels.get("main", parsed_channels.get("environment"))[
                "state"
            ]
            == "ok",
            "sensor_state_conflict",
        )
    result["failures"] = _failures(meta.get("failures"), result)
    metrics = obj(data.get(sensor), "invalid_metrics")
    require(len(metrics) <= 16, "invalid_metrics")
    required = METRICS[sensor]
    if sensor == "BME680" and not settings["gas_enabled"]:
        required = {"temperature", "humidity", "pressure"}
    require(required <= metrics.keys(), "missing_metric")
    samples = []
    for metric, item in metrics.items():
        # Display strings and unknown metric extensions do not become measurements.
        if metric not in METRICS[sensor]:
            continue
        item = obj(item, "invalid_metric")
        obs = obj(item.get("observation"), "missing_metric_observation")
        seq = number(obs.get("seq"))
        require("measured_ms" in obs, "missing_measurement_clock")
        measured = clock(obs["measured_ms"], now, nullable=True)
        state = obs.get("state")
        require(isinstance(state, str) and state in STATES, "invalid_metric_state")
        require("value" in item, "missing_value")
        value = item["value"]
        channel = parsed_channels[metric_channel(sensor, metric)]
        require(seq <= channel["ok_total"], "metric_counter_conflict")
        if seq == 0:
            require(
                value is None and measured is None and state != "ok",
                "invalid_initial_measurement",
            )
        else:
            require(
                measured is not None and state != "never", "invalid_measurement_clock"
            )
            require(
                type(value) in {int, float}
                and (type(value) is not int or abs(value) <= MAX_COUNTER)
                and math.isfinite(value),
                "invalid_value",
            )
            require(
                channel["last_ok_ms"] is not None and measured <= channel["last_ok_ms"],
                "metric_time_conflict",
            )
        if state == "ok":
            require(channel["state"] != "error", "metric_state_conflict")
        unit = item.get("units")
        require(
            unit is None or (isinstance(unit, str) and len(unit) <= 24), "invalid_unit"
        )
        samples.append(
            dict(
                metric=metric,
                value=value,
                unit=unit,
                seq=seq,
                measured_ms=measured,
                state=state,
            )
        )
    result["metrics"] = samples
    if "probes" in meta:
        result["probes"] = _probes(meta["probes"], result)
    if sensor == "ICS43434":
        result["noise"] = _noise(meta.get("noise"), result)
    elif "noise" in meta:
        require(meta["noise"] is None, "unexpected_noise")
    return result


def _failures(value: Any, snapshot: dict) -> dict:
    value = obj(value, "missing_failures")
    next_seq = number(value.get("next_seq"), minimum=1)
    recent = value.get("recent")
    require(isinstance(recent, list) and len(recent) <= 16, "invalid_failure_tail")
    require(len(recent) == min(16, next_seq - 1), "incomplete_failure_tail")
    records = []
    for i, entry in enumerate(recent):
        entry = obj(entry, "invalid_failure")
        seq = number(entry.get("seq"), minimum=1)
        cycle = number(entry.get("cycle_seq"), minimum=1)
        require(
            seq == next_seq - len(recent) + i and cycle <= snapshot["cycle_seq"],
            "failure_sequence_conflict",
        )
        at = clock(entry.get("at_ms"), snapshot["time_ms"])
        names = entry.get("channels")
        require(
            isinstance(names, list)
            and names
            and all(isinstance(n, str) and n in snapshot["channels"] for n in names)
            and len(set(names)) == len(names),
            "invalid_failure_channels",
        )
        reason = entry.get("reason")
        state_reason({"state": "error", "reason": reason})
        if records:
            require(
                cycle > records[-1]["cycle_seq"] and at >= records[-1]["at_ms"],
                "failure_order_conflict",
            )
        records.append(
            dict(
                seq=seq,
                cycle_seq=cycle,
                at_ms=at,
                channels=sorted(names),
                reason=reason,
            )
        )
    total = next_seq - 1
    require(total <= snapshot["cycle_seq"], "failure_counter_conflict")
    counters = [c["failed_total"] for c in snapshot["channels"].values()]
    require(max(counters) <= total <= sum(counters), "failure_counter_conflict")
    for name, channel in snapshot["channels"].items():
        tail = [r for r in records if name in r["channels"]]
        require(len(tail) <= channel["failed_total"], "failure_counter_conflict")
        if total <= 16:
            require(len(tail) == channel["failed_total"], "failure_counter_conflict")
        if tail:
            require(
                tail[-1]["at_ms"] == channel["last_failed_ms"], "failure_time_conflict"
            )
    return dict(next_seq=next_seq, recent=records)


def _noise(value: Any, snapshot: dict) -> dict:
    value = obj(value, "missing_noise")
    now = snapshot["time_ms"]
    started = clock(value.get("started_ms"), now)
    success = number(value.get("successful_blocks_total"))
    active = number(value.get("active_ms_total"), maximum=MAX_CLOCK)
    require("last_ok_ms" in value, "missing_noise_last_ok")
    last_ok = clock(value["last_ok_ms"], now, nullable=True)
    channel = snapshot["channels"]["main"]
    require(
        success == channel["ok_total"] and last_ok == channel["last_ok_ms"],
        "noise_channel_conflict",
    )
    require(last_ok is None or last_ok >= started, "noise_time_conflict")
    pause = obj(value.get("sds_pause"), "missing_sds_pause")
    pause_seq = number(pause.get("cycle_seq"))
    require("started_ms" in pause, "missing_pause_start")
    pause_start = clock(pause["started_ms"], now, nullable=True)
    excluded = number(pause.get("excluded_ms_total"), maximum=MAX_CLOCK)
    require(pause.get("state") in ("active", "idle"), "invalid_pause_state")
    budget = (
        snapshot["settings"]["sds_warmup_ms"] + snapshot["settings"]["sds_collect_ms"]
    )
    require(
        excluded <= pause_seq * budget
        and excluded <= now - started
        and active == now - started - excluded,
        "pause_budget_conflict",
    )
    require(pause_start is None or pause_start >= started, "pause_time_conflict")
    require(
        pause["state"] != "active"
        or (pause_seq > 0 and pause_start is not None and budget > 0),
        "invalid_active_pause",
    )
    require(
        pause_seq != 0 or (pause_start is None and excluded == 0),
        "pause_sequence_conflict",
    )
    interval = obj(value.get("interval"), "missing_noise_interval")
    interval = {
        k: number(interval.get(k), maximum=MAX_CLOCK)
        for k in (
            "start_ms",
            "end_ms",
            "max_gap_active_ms",
            "max_gap_end_ms",
            "open_gap_active_ms",
        )
    }
    require(
        started <= interval["start_ms"] <= interval["end_ms"] == now
        and interval["start_ms"] <= interval["max_gap_end_ms"] <= now
        and interval["open_gap_active_ms"] <= interval["max_gap_active_ms"] <= active
        and interval["max_gap_active_ms"] <= interval["max_gap_end_ms"] - started,
        "noise_interval_conflict",
    )
    require(
        interval["open_gap_active_ms"]
        <= now - (last_ok if last_ok is not None else started),
        "noise_open_gap_conflict",
    )
    windows = value.get("windows")
    require(isinstance(windows, list) and len(windows) <= 2, "invalid_noise_windows")
    parsed_windows = []
    for window in windows:
        window = obj(window, "invalid_noise_window")
        parsed = {
            k: number(
                window.get(k),
                maximum=MAX_COUNTER if k in {"seq", "successful_blocks"} else MAX_CLOCK,
            )
            for k in ("seq", "start_ms", "end_ms", "active_ms", "successful_blocks")
        }
        require(
            parsed["seq"] > 0
            and started <= parsed["start_ms"] < parsed["end_ms"] <= now
            and parsed["active_ms"] == 300000 <= parsed["end_ms"] - parsed["start_ms"],
            "noise_window_conflict",
        )
        if parsed_windows:
            prior = parsed_windows[-1]
            require(
                parsed["seq"] == prior["seq"] + 1
                and parsed["start_ms"] == prior["end_ms"],
                "noise_window_order",
            )
        parsed_windows.append(parsed)
    current = obj(value.get("window_current"), "missing_current_window")
    current = {
        k: number(
            current.get(k),
            maximum=MAX_COUNTER if k in {"seq", "successful_blocks"} else MAX_CLOCK,
        )
        for k in ("seq", "start_ms", "active_ms", "successful_blocks")
    }
    complete = active // 300000
    require(
        current["seq"] == complete + 1
        and current["active_ms"] == active % 300000
        and started <= current["start_ms"] <= now
        and current["active_ms"] <= now - current["start_ms"],
        "noise_current_conflict",
    )
    require(len(parsed_windows) == min(2, complete), "missing_noise_window")
    if parsed_windows:
        require(
            parsed_windows[-1]["seq"] == complete
            and parsed_windows[-1]["end_ms"] == current["start_ms"],
            "noise_current_conflict",
        )
    else:
        require(current["start_ms"] == started, "noise_current_conflict")
    require(
        sum(w["successful_blocks"] for w in parsed_windows)
        + current["successful_blocks"]
        <= success,
        "noise_success_conflict",
    )
    if complete <= 2:
        require(
            sum(w["successful_blocks"] for w in parsed_windows)
            + current["successful_blocks"]
            == success,
            "noise_success_conflict",
        )
    if budget == 0:
        require(
            pause_seq == 0 and pause_start is None and excluded == 0,
            "pause_without_sds",
        )
    require(
        interval["open_gap_active_ms"]
        >= max(0, now - (last_ok if last_ok is not None else started) - excluded),
        "noise_open_gap_conflict",
    )
    return dict(
        started_ms=started,
        successful_blocks_total=success,
        active_ms_total=active,
        last_ok_ms=last_ok,
        sds_pause=dict(
            cycle_seq=pause_seq,
            state=pause["state"],
            started_ms=pause_start,
            excluded_ms_total=excluded,
        ),
        interval=interval,
        windows=parsed_windows,
        window_current=current,
    )


def _probes(value: Any, snapshot: dict) -> dict:
    value = obj(value, "invalid_probes")
    sensor = snapshot["sensor"]
    expected = (
        {"gas"} if sensor == "BME680" else {"main"} if sensor == "ICS43434" else set()
    )
    require(bool(value) and set(value) == expected, "invalid_probe_channels")
    parsed = {}
    for name, raw in value.items():
        raw = obj(raw, "invalid_probe")
        channel = snapshot["channels"][name]
        require(
            channel["cycles_total"] == 0
            and channel["state"] == "waiting"
            and channel["reason"] == "driver_unverified",
            "probe_measurement_conflict",
        )
        p = {
            k: number(raw.get(k))
            for k in ("attempts_total", "transfers_total", "activity_total")
        }
        require(
            p["activity_total"] <= p["transfers_total"] <= p["attempts_total"],
            "probe_counter_conflict",
        )
        for stem in ("attempt", "transfer", "activity"):
            key = f"last_{stem}_ms"
            require(key in raw, "missing_probe_time")
            p[key] = clock(raw[key], snapshot["time_ms"], nullable=True)
            count = p[
                {
                    "attempt": "attempts_total",
                    "transfer": "transfers_total",
                    "activity": "activity_total",
                }[stem]
            ]
            require((count == 0) == (p[key] is None), "probe_time_conflict")
        require(
            (p["last_activity_ms"] or 0)
            <= (p["last_transfer_ms"] or 0)
            <= (p["last_attempt_ms"] or 0),
            "probe_time_conflict",
        )
        p["result"] = raw.get("result")
        require(
            isinstance(p["result"], str)
            and p["result"]
            in {
                "never",
                "init_failed",
                "read_failed",
                "short_read",
                "no_new_data",
                "no_activity",
                "activity",
            },
            "invalid_probe_result",
        )
        require(
            (p["result"] == "never") == (p["attempts_total"] == 0),
            "probe_result_conflict",
        )
        p["error_code"] = raw.get("error_code")
        require(
            type(p["error_code"]) is int and -(2**31) <= p["error_code"] < 2**31,
            "invalid_probe_error",
        )
        for key in ("bytes_read", "expected_bytes"):
            p[key] = number(raw.get(key), maximum=2**32 - 1)
        for key in ("signal_min", "signal_max", "gas_status", "gas_resistance"):
            require(key in raw, "missing_probe_detail")
            v = raw[key]
            if v is not None:
                if key == "gas_resistance":
                    v = number(v, maximum=2**32 - 1)
                else:
                    require(
                        type(v) is int
                        and (
                            -8388608 <= v <= 8388607
                            if key.startswith("signal")
                            else 0 <= v <= 255
                        ),
                        "invalid_probe_detail",
                    )
            p[key] = v
        if sensor == "ICS43434":
            require(
                p["gas_status"] is None and p["gas_resistance"] is None,
                "invalid_probe_detail",
            )
            require(
                p["expected_bytes"] in (0, 8192) and p["bytes_read"] <= 8192,
                "invalid_probe_bytes",
            )
            has_signal = p["signal_min"] is not None and p["signal_max"] is not None
            require(
                (p["signal_min"] is None) == (p["signal_max"] is None),
                "invalid_probe_signal",
            )
            if has_signal:
                require(
                    p["expected_bytes"] == p["bytes_read"] == 8192
                    and p["error_code"] == 0
                    and p["signal_min"] <= p["signal_max"],
                    "invalid_probe_signal",
                )
            if p["result"] == "activity":
                require(
                    has_signal and p["signal_min"] < p["signal_max"],
                    "invalid_probe_activity",
                )
        else:
            require(
                p["signal_min"] is None
                and p["signal_max"] is None
                and p["bytes_read"] == p["expected_bytes"] == 0,
                "invalid_probe_detail",
            )
            require(
                (p["gas_status"] is None) == (p["gas_resistance"] is None),
                "invalid_probe_detail",
            )
            if p["result"] == "activity":
                require(
                    p["gas_status"] is not None
                    and p["gas_resistance"] > 0
                    and p["error_code"] == 0,
                    "invalid_probe_activity",
                )
        if p["result"] == "activity":
            require(
                p["activity_total"] > 0
                and p["last_activity_ms"] == p["last_attempt_ms"],
                "probe_result_conflict",
            )
        parsed[name] = p
    return parsed
