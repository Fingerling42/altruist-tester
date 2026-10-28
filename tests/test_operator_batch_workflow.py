import json

import pytest
from typer.testing import CliRunner

from altruist_tester.cli import _print_worker_status, _wait_for_explicit_port, app
from altruist_tester.config import ConfigError, load_batch_config, parse_mac
from altruist_tester.ports import SerialPortInfo
from altruist_tester.progress import read_worker_status, write_worker_status
from altruist_tester.serial_logger import SerialLogProgress
from tests.cli_helpers import patch_cli_capture, release_log_stats


@pytest.mark.parametrize("urban_count,insight_count", [(2, 6), (6, 0), (0, 3)])
def test_generate_operator_batches(monkeypatch, tmp_path, urban_count, insight_count):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])
    urban = tmp_path / "urban.toml"
    insight = tmp_path / "insight.toml"
    urban.touch()
    insight.touch()
    folder = tmp_path / "batches"
    folder.mkdir()
    destination = folder / "night.toml"
    args = [
        "batch-generate",
        "--output",
        str(destination),
        "--urban-profile",
        str(urban),
        "--insight-profile",
        str(insight),
        "--port-template",
        "/dev/serial/by-id/fixture_{mac}",
    ]
    for index in range(urban_count):
        args.extend(["--urban-mac", f"AA:BB:CC:00:00:{index:02X}"])
    for index in range(insight_count):
        args.extend(["--insight-mac", f"AA:BB:CC:11:00:{index:02X}"])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "UNVERIFIED" in result.output
    batch = load_batch_config(destination)
    assert len(batch.devices) == urban_count + insight_count
    assert batch.wait_port_timeout_seconds == 120
    for device in batch.devices:
        assert device.effective_config.resolve() == (
            urban if device.model == "urban" else insight
        )
        assert device.expected_mac
    original = destination.read_bytes()
    again = CliRunner().invoke(app, args)
    assert again.exit_code == 2
    assert destination.read_bytes() == original
    preview = CliRunner().invoke(app, [*args, "--dry-run"])
    assert preview.exit_code == 0
    assert original.decode() in preview.output
    assert destination.read_bytes() == original


@pytest.mark.parametrize(
    "value", ["gAA:BB:CC:DD:EE:FF", "AABBCCDDEEF", "", "AA:BB-CC:DD:EE:FF"]
)
def test_reject_invalid_mac(value):
    with pytest.raises(ConfigError, match="Invalid MAC"):
        parse_mac(value)


def test_generate_resolves_usb_and_rejects_duplicate_and_ambiguous(
    monkeypatch, tmp_path
):
    profile = tmp_path / "profile.toml"
    profile.touch()
    port = tmp_path / "ttyUSB0"
    port.touch()
    infos = [SerialPortInfo(device=str(port), serial_number="AA:BB:CC:DD:EE:FF")]
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: infos)
    args = [
        "batch-generate",
        "--output",
        str(tmp_path / "batch.toml"),
        "--urban-profile",
        str(profile),
        "--urban-mac",
        "aabbccddeeff",
        "--dry-run",
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "USB identity verified" in result.output
    assert str(port) in result.output
    assert not (tmp_path / "batch.toml").exists()
    duplicate = CliRunner().invoke(
        app,
        [
            *args,
            "--insight-profile",
            str(profile),
            "--insight-mac",
            "AA-BB-CC-DD-EE-FF",
        ],
    )
    assert duplicate.exit_code == 2
    assert "Duplicate MAC" in duplicate.output
    infos.append(
        SerialPortInfo(device=str(tmp_path / "ttyUSB1"), serial_number="AABBCCDDEEFF")
    )
    ambiguous = CliRunner().invoke(app, args)
    assert ambiguous.exit_code == 2
    assert "Ambiguous USB" in ambiguous.output


def test_generate_missing_port_and_profile(monkeypatch, tmp_path):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])
    args = [
        "batch-generate",
        "--output",
        str(tmp_path / "batch.toml"),
        "--urban-mac",
        "AABBCCDDEEFF",
        "--urban-profile",
        str(tmp_path / "missing"),
    ]
    assert CliRunner().invoke(app, args).exit_code == 2
    profile = tmp_path / "missing"
    profile.touch()
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "No USB port found" in result.output
    result = CliRunner().invoke(app, [*args, "--port-template", "/tmp/fixed"])
    assert result.exit_code == 2
    assert "Port template must contain" in result.output


def test_infinite_wait_survives_old_timeout_then_opens(monkeypatch, tmp_path):
    port = tmp_path / "late"
    status = tmp_path / "worker.progress.json"
    now = [0.0]
    monkeypatch.setattr("altruist_tester.cli.time.monotonic", lambda: now[0])

    def connect_late(seconds):
        now[0] += 180
        if now[0] >= 540:
            port.touch()

    monkeypatch.setattr("altruist_tester.cli.time.sleep", connect_late)
    assert (
        _wait_for_explicit_port(port, timeout_seconds=None, status_file=status) == port
    )
    snapshot = read_worker_status(status)
    assert snapshot["wait_elapsed_seconds"] == 360
    assert snapshot["wait_timeout_seconds"] is None


def test_infinite_wait_can_be_cancelled(monkeypatch, tmp_path):
    def cancel(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("altruist_tester.cli.time.sleep", cancel)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(tmp_path / "missing"),
            "--duration",
            "1s",
            "--wait-port",
            "--wait-port-timeout",
            "infinite",
        ],
    )
    assert result.exit_code != 0
    assert "unlimited" in result.output


def test_finite_wait_times_out_with_status(monkeypatch, tmp_path):
    now = [0.0]
    monkeypatch.setattr("altruist_tester.cli.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "altruist_tester.cli.time.sleep", lambda seconds: now.__setitem__(0, now[0] + 1)
    )
    status = tmp_path / "status.json"
    import typer

    with pytest.raises(typer.Exit) as error:
        _wait_for_explicit_port(
            tmp_path / "absent", timeout_seconds=2, status_file=status
        )
    assert error.value.exit_code == 2
    assert read_worker_status(status)["state"] == "ERROR"


@pytest.mark.parametrize("serial_number", [None, "112233445566"])
def test_expected_mac_rejects_unknown_or_wrong_usb_before_open(
    monkeypatch, tmp_path, serial_number
):
    port = tmp_path / "ttyACM0"
    port.touch()
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [SerialPortInfo(device=str(port), serial_number=serial_number)],
    )
    opened = {}
    patch_cli_capture(monkeypatch, release_log_stats(), opened=opened)
    status = tmp_path / "status.json"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "1s",
            "--output-dir",
            str(tmp_path / "runs"),
            "--expected-mac",
            "AABBCCDDEEFF",
            "--status-file",
            str(status),
        ],
    )
    assert result.exit_code == 2
    assert not opened
    assert "refusing capture" in result.output
    assert read_worker_status(status)["state"] == "ERROR"


def test_worker_capture_status_reaches_parent(monkeypatch, tmp_path):
    from types import SimpleNamespace

    port = tmp_path / "ttyACM0"
    port.touch()
    status = tmp_path / "worker.progress.json"
    snapshots = []

    def capture_hook(serial_port, artifacts, duration_seconds, **kwargs):
        for byte_count, telemetry_count in [(0, 0), (15, 0), (100, 1)]:
            kwargs["progress_callback"](
                SerialLogProgress(
                    elapsed_seconds=5,
                    duration_seconds=60,
                    current_silence_seconds=2,
                    lines_read=int(byte_count > 0),
                    bytes_read=byte_count,
                    dev_metrics_count=telemetry_count,
                    keyword_alerts_count=0,
                    sensor_samples_count=0,
                    structured_events_count=telemetry_count,
                    wifi_state="OK" if telemetry_count else None,
                    wifi_observed_elapsed_seconds=4 if telemetry_count else None,
                )
            )
            snapshots.append(json.loads(status.read_text()))
            _print_worker_status(SimpleNamespace(slot="late", output_dir=tmp_path))

    patch_cli_capture(monkeypatch, release_log_stats(), capture_hook=capture_hook)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(port),
            "--duration",
            "60s",
            "--output-dir",
            str(tmp_path / "runs"),
            "--status-file",
            str(status),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "late CAPTURE open=yes" in result.output
    assert "first_bytes=no" in result.output
    assert "first_bytes=yes" in result.output
    assert "telemetry=unseen" in result.output
    assert "telemetry=seen" in result.output
    assert "wifi=unknown" in result.output
    assert [item["bytes_read"] for item in snapshots] == [0, 15, 100]
    assert read_worker_status(status)["state"] == "COMPLETED"
    assert not status.with_suffix(".tmp").exists()


def test_status_reader_rejects_oversized_input(tmp_path):
    status = tmp_path / "status.json"
    status.write_text(" " * 16385)
    with pytest.raises(ValueError, match="too large"):
        read_worker_status(status)


def test_manual_batch_supports_explicit_infinite_wait(tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    config = tmp_path / "batch.toml"
    config.write_text(
        '[batch]\nduration="24h"\nwait_port=true\nwait_port_timeout="infinite"\n'
        '[[devices]]\nslot="one"\nport="/dev/nonexistent"\nconfig="urban.toml"\n'
    )
    assert load_batch_config(config).wait_port_timeout_seconds is None


def test_progress_wait_does_not_claim_capture_percent(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr("altruist_tester.cli.time.monotonic", lambda: 12.0)
    write_worker_status(
        tmp_path / "worker.progress.json",
        "WAIT_PORT",
        wait_elapsed_seconds=360,
        wait_timeout_seconds=None,
    )
    _print_worker_status(SimpleNamespace(slot="late", output_dir=tmp_path))
    output = capsys.readouterr().out
    assert "late WAIT_PORT wait=06:00 | unlimited" in output
    assert "%" not in output
    assert "remaining=" not in output


@pytest.mark.parametrize(
    "snapshot",
    [
        {"state": "WAIT_PORT", "updated_monotonic": 1},
        {"state": "CAPTURE", "updated_monotonic": 1, "duration_seconds": 0},
        {"state": "ERROR", "updated_monotonic": "wrong"},
    ],
)
def test_parent_reports_invalid_worker_status_without_crashing(
    tmp_path, capsys, snapshot
):
    from types import SimpleNamespace

    (tmp_path / "worker.progress.json").write_text(json.dumps(snapshot))
    _print_worker_status(SimpleNamespace(slot="one", output_dir=tmp_path))
    assert "one STATUS_ERROR" in capsys.readouterr().out


def test_batch_passes_expected_mac_and_infinite_timeout(monkeypatch, tmp_path):
    from tests.cli_helpers import patch_batch_worker_processes, worker_arg

    profile = tmp_path / "profile.toml"
    profile.touch()
    config = tmp_path / "batch.toml"
    config.write_text(
        '[batch]\nduration="1s"\nwait_port=true\nwait_port_timeout="infinite"\n'
        f'output_dir="{tmp_path / "runs"}"\n'
        '[[devices]]\nslot="one"\nport="/dev/nonexistent"\nconfig="profile.toml"\n'
        'expected_mac="AA:BB:CC:DD:EE:FF"\n'
    )
    started = []
    patch_batch_worker_processes(monkeypatch, started=started)
    result = CliRunner().invoke(app, ["batch", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert worker_arg(started[0].args, "--expected-mac") == "AA:BB:CC:DD:EE:FF"
    assert worker_arg(started[0].args, "--wait-port-timeout") == "infinite"
    assert "--status-file" in started[0].args


def test_batch_rejects_aliases_of_the_same_port(tmp_path):
    from altruist_tester.config import (
        BatchConfig,
        BatchDeviceConfig,
        validate_batch_config,
    )

    port = tmp_path / "port"
    port.touch()
    alias = tmp_path / "alias"
    alias.symlink_to(port)
    profile = tmp_path / "profile.toml"
    profile.touch()
    config = BatchConfig(
        duration_input="1s",
        duration_seconds=1,
        devices=(
            BatchDeviceConfig(slot="one", port=port, effective_config=profile),
            BatchDeviceConfig(slot="two", port=alias, effective_config=profile),
        ),
    )
    with pytest.raises(ConfigError, match="Duplicate device resolved port"):
        validate_batch_config(config)


def test_invalid_profile_does_not_enter_infinite_wait(monkeypatch, tmp_path):
    def unexpected_sleep(seconds):
        pytest.fail("Static configuration should fail before waiting")

    monkeypatch.setattr("altruist_tester.cli.time.sleep", unexpected_sleep)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--port",
            str(tmp_path / "absent"),
            "--duration",
            "1s",
            "--wait-port",
            "--wait-port-timeout",
            "infinite",
            "--config",
            str(tmp_path / "missing.toml"),
        ],
    )
    assert result.exit_code == 2
    assert "Config file does not exist" in result.output


def test_worker_transitions_are_visible_between_periodic_updates(tmp_path, capsys):
    from types import SimpleNamespace

    worker = SimpleNamespace(slot="one", output_dir=tmp_path)
    path = tmp_path / "worker.progress.json"
    last_status = {}
    write_worker_status(
        path, "WAIT_PORT", wait_elapsed_seconds=0, wait_timeout_seconds=None
    )
    _print_worker_status(worker, last_status=last_status, force=False)
    _print_worker_status(worker, last_status=last_status, force=False)
    for byte_count, event_count in [(0, 0), (20, 0), (100, 1)]:
        write_worker_status(
            path,
            "CAPTURE",
            elapsed_seconds=1,
            duration_seconds=3,
            bytes_read=byte_count,
            current_silence_seconds=0,
            dev_metrics_count=event_count,
            sensor_samples_count=0,
            structured_events_count=event_count,
        )
        _print_worker_status(worker, last_status=last_status, force=False)
    output = capsys.readouterr().out
    assert output.count("one WAIT_PORT") == 1
    assert "first_bytes=no" in output
    assert "first_bytes=yes" in output
    assert "telemetry=seen" in output
