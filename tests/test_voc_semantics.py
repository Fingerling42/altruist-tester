"""Regression coverage for the qualitative BME680 VOC contract."""

import json
from dataclasses import replace
from datetime import datetime

import pytest

from altruist_tester.parsers.sensor_values import parse_sensor_values
from altruist_tester.rules.defaults import check_sensor_value_range
from altruist_tester.rules.engine import RuleEngineConfig, evaluate_rules
from altruist_tester.rules.flatline import check_series_flatline
from altruist_tester.serial_logger import BuildEventsSummary, SerialLogStats
from tests.helpers import sample_record, sample_series


@pytest.mark.parametrize(
    ("metric", "values"),
    [
        ("voc_index", [0, 1, 50, 75, 100]),
        ("voc_level", [0, 1, 2, 3]),
        ("voc_spikes_today", [0, 1, 65535]),
    ],
)
def test_voc_range_boundaries(metric, values):
    for value in values:
        assert check_sensor_value_range(metric, value).status == "ok"


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("voc_index", -1),
        ("voc_index", 101),
        ("voc_index", 0.5),
        ("voc_level", -2),
        ("voc_level", 4),
        ("voc_level", 1.5),
        ("voc_spikes_today", -1),
        ("voc_spikes_today", 65536),
        ("voc_spikes_today", 0.5),
    ],
)
def test_invalid_voc_values_fail(metric, value):
    assert check_sensor_value_range(metric, value).status == "fail"


def test_voc_unavailable_is_diagnostic_even_for_one_sample():
    finding = check_sensor_value_range("voc_level", -1)
    assert finding.status == "warn"
    assert finding.value == -1
    assert "unavailable" in finding.message
    assert "invalid gas reading" in finding.message


@pytest.mark.parametrize("sensor", ["BME680", "bme_680", "BME-680"])
@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("voc_index", 0),
        ("voc_index", 100),
        ("voc_level", 0),
        ("voc_level", 3),
        ("voc_spikes_today", 0),
        ("voc_spikes_today", 65535),
    ],
)
def test_constant_voc_indicators_do_not_require_pollution_or_variation(
    sensor, metric, value
):
    records = [sample_record(sensor, metric, value, t) for t in (0, 86400)]
    finding = check_series_flatline(sensor, metric, records)
    assert finding.status == "ok"
    assert finding.samples_count == 2
    assert finding.distinct_values_count == 1
    assert finding.elapsed_seconds == 86400
    assert "variation is optional" in finding.message


@pytest.mark.parametrize(
    ("sensor", "metric", "value"),
    [
        ("BME680", "gas_resistance", 0),
        ("BME680", "gas_resistance", 120000),
        ("BME680", "gas_baseline", 120000),
        ("BME680", "temperature", 25),
        ("SCD4x", "co2", 600),
        ("other", "voc_index", 0),
    ],
)
def test_voc_exception_does_not_relax_other_series(sensor, metric, value):
    records = [sample_record(sensor, metric, value, t) for t in (0, 3600)]
    assert check_series_flatline(sensor, metric, records).status == "fail"


def _evaluate_voc(values, *, reboot=False):
    series = sample_series()
    for offset, snapshot in enumerate(values):
        for metric, value in snapshot.items():
            series.append(sample_record("BME680", metric, value, offset * 300))
    return evaluate_rules(
        SerialLogStats(
            lines_read=len(values),
            bytes_read=100,
            first_line_elapsed_seconds=0,
            last_line_elapsed_seconds=(len(values) - 1) * 300,
            max_interline_gap_seconds=60,
            sensor_series=series,
            dev_metrics_records=(
                {"boot": 1, "uptime_sec": 300, "reset_reason": "power_on_reset"},
                {
                    "boot": 2 if reboot else 1,
                    "uptime_sec": 10 if reboot else 600,
                    "reset_reason": "power_on_reset",
                },
            ),
            build_events=BuildEventsSummary(
                count=1, last_build={"model": "insight", "profile": "debug"}
            ),
        ),
        RuleEngineConfig(
            expected_metrics=("voc_index", "voc_level", "voc_spikes_today"),
            duration_seconds=(len(values) - 1) * 300,
        ),
    )


def test_hour_of_normal_voc_constants_passes_rules_engine():
    result = _evaluate_voc([dict(voc_index=0, voc_level=0, voc_spikes_today=0)] * 13)
    assert result.verdict == "PASS_CANDIDATE"
    assert result.reports.sensor_flatlines.failure_count == 0
    assert result.reports.sensor_ranges.status == "ok"


def test_day_rollover_and_persisted_counter_do_not_require_monotonicity():
    # Host dates cannot establish the device's local calendar day or time sync.
    result = _evaluate_voc(
        [
            dict(voc_index=0, voc_level=0, voc_spikes_today=count)
            for count in (5, 5, 0, 0, 1)
        ]
    )
    assert result.verdict == "PASS_CANDIDATE"


def test_reboot_remains_failure_whether_daily_counter_is_restored_or_reset():
    for count in (0, 7):
        result = _evaluate_voc(
            [
                dict(voc_index=0, voc_level=0, voc_spikes_today=7),
                dict(voc_index=0, voc_level=0, voc_spikes_today=count),
            ],
            reboot=True,
        )
        assert result.verdict == "FAIL"
        assert result.failed_checks == ("runtime_counters",)
        assert result.reports.sensor_flatlines.status == "ok"


def test_warmup_zeros_then_valid_index_are_not_invalid_readings():
    result = _evaluate_voc(
        [
            dict(voc_index=index, voc_level=level, voc_spikes_today=0)
            for index, level in [(0, 0)] * 4 + [(50, 2)]
        ]
    )
    assert result.verdict == "PASS_CANDIDATE"


def test_unavailable_level_is_not_hidden_by_later_recovery():
    result = _evaluate_voc(
        [
            dict(voc_index=0, voc_level=-1, voc_spikes_today=0),
            dict(voc_index=0, voc_level=0, voc_spikes_today=0),
        ]
    )
    assert result.verdict == "WARN"
    ranges = result.reports.sensor_ranges.as_dict()
    assert ranges["warning_count"] == 1
    assert ranges["findings"][0]["value"] == -1
    assert "unavailable" in ranges["findings"][0]["message"]


def test_invalid_constant_index_fails_ranges_despite_flatline_exception():
    result = _evaluate_voc([dict(voc_index=101, voc_level=0, voc_spikes_today=0)] * 13)
    assert result.verdict == "FAIL"
    assert result.failed_checks == ("sensor_ranges",)
    assert result.reports.sensor_ranges.failure_count == 13


def test_json_preserves_voc_and_gas_evidence_including_unavailable_level():
    metrics = dict(
        voc_index=0,
        voc_level=-1,
        voc_spikes_today=7,
        gas_resistance=0,
        gas_baseline=123456,
    )
    payload = {
        "BME680": {
            metric: {
                "value": value,
                "units": "Ohm" if metric.startswith("gas_") else "",
            }
            for metric, value in metrics.items()
        }
    }
    payload["BME680"]["voc_phrase"] = {"value": "No VOC detected"}
    samples = parse_sensor_values(json.dumps(payload))
    assert {sample.metric: sample.value for sample in samples} == metrics
    assert all(
        sample.unit == "Ohm" for sample in samples if sample.metric.startswith("gas_")
    )
    assert all(sample.source == "serial" for sample in samples)


@pytest.mark.parametrize(
    ("level", "exit_code", "range_status"),
    [(0, 0, "ok"), (-1, 0, "warn"), (4, 1, "fail")],
)
def test_cli_reports_voc_policy_in_summary_and_report(
    monkeypatch, tmp_path, level, exit_code, range_status
):
    from typer.testing import CliRunner

    from altruist_tester.cli import app
    from tests.cli_helpers import patch_cli_capture, release_log_stats

    port = tmp_path / "ttyACM0"
    port.touch()
    series = sample_series(
        *[
            sample_record("BME680", metric, value, offset)
            for offset in range(0, 3601, 300)
            for metric, value in dict(
                voc_index=0, voc_level=level, voc_spikes_today=0
            ).items()
        ]
    )
    stats = replace(
        release_log_stats(sensor_series=series),
        first_line_elapsed_seconds=0,
        last_line_elapsed_seconds=3600,
        max_interline_gap_seconds=60,
    )
    patch_cli_capture(monkeypatch, stats)
    finished_at = datetime.fromisoformat(series.by_key[("BME680", "voc_level")][-1].ts)
    monkeypatch.setattr("altruist_tester.cli.utc_now", lambda: finished_at)
    output_dir = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "1h",
            "--expect-metric",
            "voc_level",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == exit_code, result.output
    run_dir = next(output_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["sensor_flatlines"]["status"] == "ok"
    assert summary["sensor_ranges"]["status"] == range_status
    report = (run_dir / "report.txt").read_text()
    if level == -1:
        assert "VOC unavailable" in report
        assert any(
            "VOC unavailable" in finding["message"] for finding in summary["findings"]
        )
    elif level == 4:
        assert "outside voc_level" in report


def test_voc_constants_still_fail_when_updates_are_missing():
    result = _evaluate_voc(
        [
            dict(voc_index=0, voc_level=0, voc_spikes_today=0),
            *([{}] * 11),
            dict(voc_index=0, voc_level=0, voc_spikes_today=0),
        ]
    )
    assert result.verdict == "FAIL"
    assert result.failed_checks == ("sensor_cadence",)
    assert result.reports.sensor_flatlines.status == "ok"


def test_single_voc_value_does_not_establish_a_sufficient_series():
    finding = check_series_flatline(
        "BME680", "voc_index", [sample_record("BME680", "voc_index", 0, 0)]
    )
    assert finding.status == "warn"
    assert "not enough samples" in finding.message
