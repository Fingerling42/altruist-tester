import io
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from altruist_tester.batch_terminal import BatchTerminal
from altruist_tester.cli import _wait_for_batch_workers, app
from altruist_tester.progress import write_worker_status
from tests.cli_helpers import patch_batch_worker_processes


class TerminalOutput(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("interrupt", [False, True])
def test_terminal_restores_screen_and_cursor(monkeypatch, interrupt):
    output = TerminalOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(
        "altruist_tester.batch_terminal.shutil.get_terminal_size",
        lambda **kwargs: os.terminal_size((80, 24)),
    )
    try:
        with BatchTerminal() as terminal:
            terminal.draw("elapsed=00:01", ["one WAIT_PORT\x1b[2J | unlimited"])
            if interrupt:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    text = output.getvalue()
    assert text.startswith("\x1b[?1049h\x1b[?25l")
    assert "one WAIT_PORT [2J | unlimited" in text
    assert "\x1b[2J" not in text
    assert "\x1b[?25h\x1b[?1049l" in text
    assert text.endswith("one WAIT_PORT [2J | unlimited\n")


def test_small_terminal_and_resize(monkeypatch):
    output = TerminalOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm")
    size = [20, 5]
    monkeypatch.setattr(
        "altruist_tester.batch_terminal.shutil.get_terminal_size",
        lambda **kwargs: os.terminal_size(size),
    )
    with BatchTerminal() as terminal:
        terminal.draw("Batch", ["one " + "x" * 100, "two OPENING"])
        first = output.getvalue().split("\x1b[H")[-1].removesuffix("\x1b[J")
        assert len(first.splitlines()) <= 5
        assert "Clipped" in first
        size[:] = [100, 24]
        terminal.draw("Batch", ["one CAPTURE open=yes", "two OPENING"])
    assert "one CAPTURE open=yes" in output.getvalue()
    assert "two OPENING" in output.getvalue()


@pytest.mark.parametrize("tty,term", [(False, "xterm"), (True, "dumb")])
def test_plain_output_has_no_terminal_controls(monkeypatch, tty, term):
    output = TerminalOutput() if tty else io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", term)
    with BatchTerminal() as terminal:
        assert not terminal.enabled
    assert "\x1b" not in output.getvalue()


def test_tty_monitor_tracks_independent_slots_and_errors(monkeypatch, tmp_path):
    output = TerminalOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm")
    ticks = [0]
    monkeypatch.setattr("altruist_tester.cli.time.monotonic", lambda: ticks[0])
    frames = []
    draw = BatchTerminal.draw

    def record_draw(self, header, slots):
        frames.append((header, list(slots)))
        draw(self, header, slots)

    monkeypatch.setattr(BatchTerminal, "draw", record_draw)
    workers = []
    for slot in ("one", "two"):
        directory = tmp_path / slot
        directory.mkdir()
        stderr = directory / "worker.stderr.log"
        stderr.touch()
        workers.append(
            SimpleNamespace(
                slot=slot,
                command=[],
                output_dir=directory,
                stdout_log=directory / "stdout",
                stderr_log=stderr,
                process=SimpleNamespace(poll=lambda: None),
            )
        )
    path = workers[0].output_dir / "worker.progress.json"
    other = workers[1].output_dir / "worker.progress.json"
    write_worker_status(
        path, "WAIT_PORT", wait_elapsed_seconds=0, wait_timeout_seconds=None
    )
    write_worker_status(
        other, "WAIT_PORT", wait_elapsed_seconds=0, wait_timeout_seconds=20
    )

    def advance(seconds):
        ticks[0] += 1
        if ticks[0] == 1:
            write_worker_status(path, "OPENING", message="/dev/test")
        elif ticks[0] in (2, 3, 4):
            write_worker_status(
                path,
                "CAPTURE",
                elapsed_seconds=ticks[0] - 2,
                duration_seconds=10,
                bytes_read=0 if ticks[0] == 2 else 20,
                current_silence_seconds=0,
                dev_metrics_count=int(ticks[0] == 4),
                sensor_samples_count=int(ticks[0] == 4),
                structured_events_count=int(ticks[0] == 4),
                wifi_state="OK" if ticks[0] == 4 else None,
                wifi_observed_elapsed_seconds=2 if ticks[0] == 4 else None,
            )
        elif ticks[0] == 5:
            write_worker_status(path, "COMPLETED", message="Done")
            workers[0].process.poll = lambda: 0
            other.write_text("broken")
        elif ticks[0] == 6:
            write_worker_status(
                other, "ERROR", message="Expected USB MAC AA, observed BB"
            )
            workers[1].process.poll = lambda: 2

    monkeypatch.setattr("altruist_tester.cli.time.sleep", advance)
    results = []
    _wait_for_batch_workers(
        workers, results, duration_seconds=10, batch_dir=tmp_path, slots=("one", "two")
    )
    text = output.getvalue()
    for expected in (
        "one WAIT_PORT",
        "unlimited",
        "remaining=",
        "one OPENING",
        "open=yes",
        "first_bytes=no",
        "first_bytes=yes",
        "telemetry=seen",
        "health=1",
        "samples=1",
        "wifi=OK (health age=00:00)",
        "two STATUS_ERROR",
        "code=2",
        "Expected USB MAC AA, observed BB",
    ):
        assert expected in text
    assert all("%" not in header for header, _ in frames)
    assert [r["returncode"] for r in results] == [0, 2]
    assert frames[-1][1][0].startswith("one EXIT COMPLETED code=0")


@pytest.mark.parametrize("source", ["summary", "snapshot", "stderr", "start"])
def test_batch_report_preserves_infrastructure_reason(monkeypatch, tmp_path, source):
    profile = tmp_path / "profile.toml"
    profile.touch()
    config = tmp_path / "batch.toml"
    config.write_text(
        '[batch]\nduration="1s"\n'
        f'output_dir="{tmp_path / "runs"}"\ndevice_config="profile.toml"\n'
        '[[devices]]\nslot="one"\nport="/dev/test-one"\nmodel="urban"\n'
        '[[devices]]\nslot="two"\nport="/dev/test-two"\nmodel="urban"\n'
    )
    reason = "Expected USB MAC AA:BB:CC:DD:EE:FF, observed 11:22:33:44:55:66"
    started = []
    patch_batch_worker_processes(
        monkeypatch,
        started=started,
        returncodes_by_port={"/dev/test-one": 2},
        summaries_by_port={"/dev/test-one": {"status": "failed", "message": reason}},
    )
    from altruist_tester import cli

    start = cli._start_batch_worker

    def start_worker(batch, device):
        if device.slot == "one" and source == "start":
            raise OSError(reason)
        worker = start(batch, device)
        if device.slot == "one":
            if source == "summary":
                run_dir = worker.output_dir / "run"
                run_dir.mkdir()
                (run_dir / "summary.json").write_text(
                    json.dumps({"status": "failed", "message": reason})
                )
            if source != "summary":
                for path in worker.output_dir.glob("*/summary.json"):
                    path.unlink()
            if source == "snapshot":
                write_worker_status(
                    worker.output_dir / "worker.progress.json", "ERROR", message=reason
                )
            if source == "stderr":
                worker.stderr_log.write_text(reason)
        return worker

    monkeypatch.setattr(cli, "_start_batch_worker", start_worker)
    result = CliRunner().invoke(app, ["batch", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert "\x1b" not in result.output
    batch_dir = next((tmp_path / "runs").iterdir())
    report = (batch_dir / "batch_report.txt").read_text()
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert reason in report
    assert (
        "worker exit code: 2" in report
        if source != "start"
        else "worker_start_failed" in report
    )
    assert summary["devices_failed"] == 1
    assert summary["devices_passed"] == 1
    assert summary["device_results"][0]["worker_error"] == reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
@pytest.mark.parametrize("interrupt", [False, True])
def test_real_pty_batch_restores_terminal(tmp_path, interrupt):
    import pty
    import select

    profile = tmp_path / "profile.toml"
    profile.touch()
    config = tmp_path / "batch.toml"
    config.write_text(
        '[batch]\nduration="1s"\nwait_port=true\n'
        f'wait_port_timeout="{"infinite" if interrupt else "1s"}"\n'
        f'output_dir="{tmp_path / "runs"}"\ndevice_config="profile.toml"\n'
        f'[[devices]]\nslot="late"\nport="{tmp_path / "absent"}"\nmodel="urban"\n'
    )
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from altruist_tester.cli import app; app()",
            "batch",
            "--config",
            str(config),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            if interrupt and not sent and b"WAIT_PORT" in output:
                process.send_signal(signal.SIGTERM)
                sent = True
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    text = output.decode(errors="replace")
    assert "\x1b[?1049h\x1b[?25l" in text
    assert "WAIT_PORT" in text
    assert "\x1b[?25h\x1b[?1049l" in text
    assert process.returncode == (130 if interrupt else 1), text
    report = next((tmp_path / "runs").glob("*/batch_report.txt")).read_text()
    assert "interrupted" in report if interrupt else "worker exit code: 2" in report
