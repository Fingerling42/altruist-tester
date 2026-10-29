import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from altruist_tester.batch_terminal import BatchSlotStatus, BatchTerminal
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
            terminal.draw(
                "elapsed=00:01",
                [
                    BatchSlotStatus(
                        "one", "WAIT_PORT", {"message": "\x1b[2J | unlimited"}
                    )
                ],
            )
            if interrupt:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    text = output.getvalue()
    assert text.startswith("\x1b[?1049h\x1b[?25l")
    assert "one:  [2J | unlimited" in text
    assert "\x1b[2J" not in text
    assert "\x1b[?25h\x1b[?1049l" in text
    assert text.endswith("\x1b[?25h\x1b[?1049l")
    assert text.count("elapsed=00:01") == 1


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
        terminal.draw(
            "Batch",
            [
                BatchSlotStatus("one", "ERROR", {"message": "x" * 100}),
                BatchSlotStatus("two", "OPENING", {}),
            ],
        )
        first = output.getvalue().split("\x1b[H")[-1].removesuffix("\x1b[J")
        assert len(first.splitlines()) <= 5
        assert "Clipped" in first
        size[:] = [100, 24]
        terminal.draw(
            "Batch",
            [
                BatchSlotStatus("one", "CAPTURE", {"open": "yes"}),
                BatchSlotStatus("two", "OPENING", {}),
            ],
        )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
    assert re.search(r"one +CAPTURE +yes", plain)
    assert re.search(r"two +OPENING", plain)


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
        frames.append((header, [row.line() for row in slots]))
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
    text = "\n".join(line for _, rows in frames for line in rows)
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
        "exit=2",
        "Expected USB MAC AA, observed BB",
    ):
        assert expected in text
    assert all("%" not in header for header, _ in frames)
    assert [r["returncode"] for r in results] == [0, 2]
    assert frames[-1][1][0].startswith("one COMPLETED")
    assert "exit=0" in frames[-1][1][0]
    assert "status_age=—" in frames[-1][1][0]
    assert "status_age=—" in frames[-1][1][1]
    assert "status_age=00:" not in frames[-1][1][0]


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
    final = text.split("\x1b[?1049l")[-1]
    assert "RESULT" in final
    assert "Report:" in final
    assert "status_age=" not in final
    assert "Captured" not in final
    report = next((tmp_path / "runs").glob("*/batch_report.txt")).read_text()
    assert "interrupted" in report if interrupt else "worker exit code: 2" in report


@pytest.mark.parametrize("no_color", [False, True])
def test_tty_table_is_compact_aligned_and_colored(monkeypatch, no_color):
    output = TerminalOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm-256color")
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    size = [190, 60]
    monkeypatch.setattr(
        "altruist_tester.batch_terminal.shutil.get_terminal_size",
        lambda **kwargs: os.terminal_size(size),
    )
    fields = {
        "open": "yes",
        "capture": "00:03/03:00",
        "progress": "2.0%",
        "bytes": "105",
        "first_bytes": "yes",
        "quiet": "00:03",
        "telemetry": "unseen",
        "health": "0",
        "samples": "0",
        "wifi": "unknown",
        "status_age": "00:00",
    }
    rows = [
        BatchSlotStatus("urban-1051db010dc0", "CAPTURE", fields),
        BatchSlotStatus(
            "insight-206ef108edac",
            "CAPTURE",
            {
                **fields,
                "bytes": "123456",
                "health": "12",
                "samples": "120",
                "wifi": "OK",
                "wifi_age": "01:02",
                "telemetry": "seen",
            },
        ),
        BatchSlotStatus(
            "late", "WAIT_PORT", {"wait": "00:04", "remaining": "unlimited"}
        ),
        BatchSlotStatus("failed", "FAILED", {"exit": "2", "message": "USB mismatch"}),
    ]
    with BatchTerminal() as terminal:
        terminal.draw("Batch elapsed=00:04", rows)
        frame = output.getvalue().split("\x1b[H")[-1].removesuffix("\x1b[J")
        plain = re.sub(r"\x1b\[[0-9;]*m", "", frame)
        lines = plain.splitlines()
        assert lines[1].startswith("SLOT")
        assert lines[2].startswith("urban-")
        assert lines[3].startswith("insight-")
        assert lines[4].startswith("late ")
        assert lines[5].startswith("failed ")
        assert lines[2].index("CAPTURE") == lines[3].index("CAPTURE")
        for column, first, second in (
            ("BYTES", "105", "123456"),
            ("HEALTH", "0", "12"),
            ("SAMPLES", "0", "120"),
            ("WIFI", "unknown", "OK"),
        ):
            offset = lines[1].index(column)
            assert lines[2][offset:].startswith(first)
            assert lines[3][offset:].startswith(second)
        assert "failed: USB mismatch" in plain
        assert len(lines) < 12  # A tall terminal must not add space between devices.
        assert ("\x1b[1;32m" in frame) is not no_color
        assert ("\x1b[1;31m" in frame) is not no_color
        assert ("\x1b[1;36m" in frame) is not no_color
        # Changing only terminal height must preserve the compact layout.
        size[1] = 20
        terminal.draw("Batch elapsed=00:04", rows)
        second_frame = output.getvalue().split("\x1b[H")[-1].removesuffix("\x1b[J")
        assert second_frame == frame


@pytest.mark.parametrize("tty", [False, True])
def test_final_results_show_verdicts_and_short_reasons(monkeypatch, tmp_path, tty):
    from altruist_tester.cli import _print_batch_result

    output = TerminalOutput() if tty else io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(
        "altruist_tester.cli.shutil.get_terminal_size",
        lambda **kwargs: os.terminal_size((120, 24)),
    )
    base = {"worker_returncode": 0, "findings_count": 0}
    devices = [
        {**base, "slot": "urban-pass", "verdict": "PASS_CANDIDATE"},
        {
            **base,
            "slot": "urban-warn",
            "verdict": "WARN",
            "finding_messages": [
                "OPTIONAL_UPLOAD: No upload",
                "NO_HEALTH: No HEALTH",
                "OTHER: Extra warning",
            ],
        },
        {
            **base,
            "slot": "insight-fail",
            "verdict": "FAIL",
            "worker_returncode": 1,
            "failure_kind": "health_check_failed",
            "finding_messages": [
                "SENSOR_MISSING: Missing CO2",
                "SUBSYSTEM_FAILED: Sensor failed",
            ],
            "worker_error": "FAIL: Captured 90 serial lines " * 50,
        },
        {
            **base,
            "slot": "usb-error",
            "worker_returncode": 2,
            "failure_kind": "infrastructure_or_config_failed",
            "summary_error": "summary.json not found",
            "worker_error": (
                "Serial capture failed on /dev/serial/by-id/"
                "usb-Espressif_USB_JTAG_serial_debug_unit_20:6E:F1:08:ED:AC-if00: "
                "Expected USB MAC AA, observed BB; refusing capture"
            ),
        },
        {
            **base,
            "slot": "start-error",
            "worker_returncode": None,
            "failure_kind": "worker_start_failed",
            "worker_error": "Permission denied",
        },
        {
            **base,
            "slot": "cancelled",
            "worker_returncode": -15,
            "failure_kind": "interrupted",
        },
    ]
    _print_batch_result(
        {
            "devices": list(reversed(devices)),
            "devices_passed": 1,
            "devices_warned": 1,
            "devices_failed": 4,
        },
        tmp_path,
        slots=tuple(device["slot"] for device in devices),
    )
    raw = output.getvalue()
    text = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    lines = text.splitlines()
    assert lines[0] == "Results: 1 pass, 1 warn, 4 fail"
    assert [line.split()[0] for line in lines[2:8]] == [
        device["slot"] for device in devices
    ]
    assert "usb-Espressif_USB_JTAG_serial_debug_unit" not in text
    for slot, verdict, code in (
        ("urban-pass", "PASS_CANDIDATE", "0"),
        ("urban-warn", "WARN", "0"),
        ("insight-fail", "FAIL", "1"),
        ("usb-error", "ERROR", "2"),
        ("start-error", "ERROR", "-"),
        ("cancelled", "INTERRUPTED", "-15"),
    ):
        line = next(line for line in lines if line.startswith(slot))
        assert line.split()[:3] == [slot, verdict, code]
        assert len(line) <= 120
    assert "OPTIONAL_UPLOAD, NO_HEALTH (+1)" in text
    assert "SENSOR_MISSING, SUBSYSTEM_FAILED" in text
    assert "Expected USB MAC AA, observed BB" in text
    assert "Permission denied" in text
    assert "Captured 90 serial lines" not in text
    assert "summary.json not found" not in text  # Prefer the actual worker error.
    assert text.count("Report:") == 1
    assert str(tmp_path / "batch_report.txt") in text
    assert ("\x1b[" in raw) is tty


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Serial capture failed on /dev/serial/by-id/usb_20:6E:F1:08:ED:AC: "
            "device reports readiness to read but returned no data "
            "(device disconnected or multiple access on port?)",
            "device reports readiness to read but returned no data "
            "(device disconnected or multiple access on port?)",
        ),
        (
            "Serial capture failed on /dev/ttyACM0: permission denied",
            "permission denied",
        ),
        (
            "Could not start worker: permission denied",
            "Could not start worker: permission denied",
        ),
        (
            "Serial capture failed on unavailable",
            "Serial capture failed on unavailable",
        ),
    ],
)
def test_compact_worker_error_preserves_cause(message, expected):
    from altruist_tester.batch_terminal import compact_worker_error

    assert compact_worker_error(message) == expected


def test_redraw_clears_old_columns_after_wait_and_wifi_changes(monkeypatch):
    output = TerminalOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        "altruist_tester.batch_terminal.shutil.get_terminal_size",
        lambda **kwargs: os.terminal_size((180, 24)),
    )

    def screen_after(stream):
        # Model cursor movement and erase semantics, not just emitted strings:
        # erase-to-end-of-screen does not clear tails of earlier rows.
        screen = [[" "] * 180 for _ in range(24)]
        row = column = 0
        for token in re.split(r"(\x1b\[[0-?]*[ -/]*[@-~])", stream):
            if token == "\x1b[H":
                row = column = 0
            elif token == "\x1b[K":
                screen[row][column:] = [" "] * (180 - column)
            elif token == "\x1b[J":
                screen[row][column:] = [" "] * (180 - column)
                for line in screen[row + 1 :]:
                    line[:] = [" "] * 180
            elif not token.startswith("\x1b["):
                for char in token:
                    if char == "\r":
                        column = 0
                    elif char == "\n":
                        row += 1
                    else:
                        screen[row][column] = char
                        column += 1
        return ["".join(line).rstrip() for line in screen]

    capture = {
        "open": "yes",
        "capture": "00:23/03:00",
        "progress": "13.1%",
        "bytes": "1794",
        "first_bytes": "yes",
        "quiet": "00:00",
        "telemetry": "seen",
        "health": "0",
        "samples": "0",
        "wifi": "unknown",
        "status_age": "00:00",
    }
    frames = [
        [
            BatchSlotStatus(
                "urban",
                "CAPTURE",
                capture,
            ),
            BatchSlotStatus(
                "insight",
                "WAIT_PORT",
                {
                    "wait": "00:47",
                    "wait_limit": "unlimited",
                    "status_age": "00:00",
                },
            ),
        ],
        [
            BatchSlotStatus("urban", "CAPTURE", capture),
            BatchSlotStatus("insight", "CAPTURE", capture),
        ],
        [
            BatchSlotStatus(
                "urban", "CAPTURE", {**capture, "wifi": "OK", "wifi_age": "00:12"}
            ),
            BatchSlotStatus("insight", "CAPTURE", capture),
        ],
        [
            BatchSlotStatus("urban", "COMPLETED", {"exit": "0", "status_age": "—"}),
            BatchSlotStatus("insight", "COMPLETED", {"exit": "0", "status_age": "—"}),
        ],
    ]
    terminal = BatchTerminal()
    previous = ""
    for index, slots in enumerate(frames):
        output.seek(0)
        output.truncate()
        terminal.draw("Batch", slots)
        current = output.getvalue()
        assert screen_after(previous + current) == screen_after(current)
        if index:
            visible = "\n".join(screen_after(previous + current))
            assert "unlimited" not in visible
            assert "nlimited" not in visible
            assert "REMAINING" not in visible
        previous += current
