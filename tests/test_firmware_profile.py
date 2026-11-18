import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from altruist_tester.cli import app
from altruist_tester.rules.log_contract import firmware_profile_error
from altruist_tester.serial_logger import BuildEventsSummary, SerialLogStats
from tests.cli_helpers import patch_cli_capture, release_log_stats


def build_stats(profile, *, telemetry=False, channel="testing", late=False):
    build = {"profile": profile, "channel": channel, "commit": "abcdef0"}
    timestamp = "2026-11-18T12:20:00Z" if late else "2026-11-18T12:00:00Z"
    return replace(
        release_log_stats()
        if telemetry
        else SerialLogStats(lines_read=0, bytes_read=0),
        capture_started_at="2026-11-18T12:00:00Z",
        build_events=BuildEventsSummary(
            count=1, first_seen=timestamp, last_build=build
        ),
        build_event_records=({"ts": timestamp, **build},),
    )


@pytest.mark.parametrize("channel", ["stable", "testing"])
@pytest.mark.parametrize(
    "profile,telemetry,code",
    [
        ("debug", False, 1),
        ("debug", True, 0),
        ("release", False, 2),
        ("release", True, 0),
        ("mystery", True, 2),
        (None, False, 1),
        (None, True, 0),
    ],
)
@pytest.mark.parametrize("late", [False, True])
def test_run_profile_contract(
    monkeypatch, tmp_path, profile, telemetry, code, channel, late
):
    stats = build_stats(profile, telemetry=telemetry, channel=channel, late=late)
    patch_cli_capture(monkeypatch, stats)
    port = tmp_path / "port"
    port.touch()
    output = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        ["run", "--port", str(port), "--duration", "5s", "--output-dir", str(output)],
    )
    assert result.exit_code == code, result.output
    run = next(output.iterdir())
    summary = json.loads((run / "summary.json").read_text())
    report = (run / "report.txt").read_text()
    if code == 2:
        assert "verdict" not in summary
        assert "sensor_presence" not in summary
        assert "not evaluated" in report
        assert summary["build_event_records"] == list(stats.build_event_records)
        events = [
            json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()
        ]
        assert any(e.get("reason") == "firmware_profile" for e in events)
    else:
        codes = {f["code"] for f in summary["findings"]}
        if not telemetry:
            assert "LOG_CONTRACT_HEALTH_MISSING" in codes
            assert "LOG_CONTRACT_SENSOR_SAMPLES_MISSING" in codes
        if profile is None:
            assert "LOG_CONTRACT_BUILD_PROFILE_MISSING" in codes
        if late:
            assert "LOG_CONTRACT_FIRMWARE_BUILD_INFO_LATE" in codes


@pytest.mark.parametrize(
    "key", ["profile", "commit", "model", "target", "version", "channel", "language"]
)
def test_conflicting_build_metadata_is_rejected(key):
    stats = build_stats("debug", telemetry=True)
    stats = replace(stats, build_event_records=({key: "first"}, {key: "second"}))
    assert "Conflicting [BUILD]" in firmware_profile_error(stats)


@pytest.mark.parametrize("telemetry", [False, True])
def test_missing_build_never_implies_release(telemetry):
    stats = (
        release_log_stats() if telemetry else SerialLogStats(lines_read=0, bytes_read=0)
    )
    assert firmware_profile_error(stats) is None


@pytest.mark.parametrize("missing", ["health", "samples"])
def test_partial_release_contract_is_unsuitable(missing):
    stats = build_stats("release", telemetry=True)
    if missing == "health":
        stats = replace(stats, dev_metrics_records=())
    else:
        stats = replace(
            stats,
            sensor_series=SerialLogStats(lines_read=0, bytes_read=0).sensor_series,
            sensor_samples_count=0,
        )
    assert "not evidence of a sensor defect" in firmware_profile_error(stats)
