"""Synthetic fixtures only: all sockets, discovery and serial opens are blocked."""

import json
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import serial
from serial.tools import list_ports

from altruist_tester import diagnostics as diag
from altruist_tester.diagnostic_checks import (
    LogPage,
    State,
    UartSummary,
    persistence_check,
    reboot_check,
    safe_fields,
    uart_check,
)

SECRET = "FICTITIOUS_CREDENTIAL_739_DO_NOT_USE"
ID = "001122334455"
COMMIT = "abc1234"
BUILD = (
    "[BUILD] version=R-INS_test-testing+abc1234 channel=testing "
    "commit=abc1234 model=insight target=esp32c6 language=en profile=debug"
)
VOC = {"voc_index", "voc_level", "voc_spikes_today", "gas_resistance", "gas_baseline"}
SENSOR = json.dumps({"BME680": {key: {"value": 1, "units": ""} for key in VOC}})
HEALTH = (
    "[HEALTH] uptime=100 boot=1 heap=100 rssi=-50 tx=1 errors=0 "
    "wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0"
)


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("live_io_forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(serial, "Serial", forbidden)
    monkeypatch.setattr(list_ports, "comports", forbidden)


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def deadline(clock, seconds=120):
    return diag.Deadline(seconds, clock=clock.now, sleep=clock.sleep)


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            json.dumps(
                {
                    "sensor_id": ID,
                    "ip": "192.168.1.2",
                    "wlanpwd": SECRET,
                    "www_password": SECRET,
                }
            ),
            {"device_id": ID, "ip": "192.168.1.2"},
        ),
        (f'{{"config":"{SECRET}","host":"192.168.1.2"}}', {}),
        ("[1] [INFO] WiFi connected, IP is: : 192.168.1.2", {"ip": "192.168.1.2"}),
        (f"{SECRET} 192.168.1.2", {}),
        (f"sensor_id={SECRET}", {}),
        ("ChipId: 00:11:22:33:44:55", {"device_id": ID}),
        (f"[LOG] status uptime=10 boot=2 password={SECRET}", {"uptime": 10, "boot": 2}),
        (
            json.dumps({"sensor_id": SECRET, "software_version": SECRET, "ip": SECRET}),
            {},
        ),
        (f"{{invalid {SECRET}", {}),
    ],
)
def test_allowlist_before_output(text, expected):
    fields = safe_fields(text)
    assert fields == expected
    assert SECRET not in json.dumps(fields)


def test_build_allowlist_omits_raw_fields_and_unvalidated_values():
    fields = safe_fields(BUILD + f" credential={SECRET}")
    assert fields["commit"] == COMMIT
    assert fields["profile"] == "debug"
    assert SECRET not in json.dumps(fields)
    assert "raw_fields" not in fields and "version" not in fields
    assert "profile" not in safe_fields(
        BUILD.replace("profile=debug", f"profile={SECRET}")
    )


def test_uart_json_uses_sensor_parser_not_api_shape():
    summary = UartSummary()
    data = (BUILD + "\n" + HEALTH + "\n" + SENSOR + "\n").encode()
    for index in range(0, len(data), 7):
        summary.feed(data[index : index + 7])
    value = summary.finish()
    assert value["sensor_json_records"] == 5
    assert uart_check(value, "debug", "insight", None)["status"] == "PASS"
    assert value["fields"]["boot"] == 1


@pytest.mark.parametrize(
    "line",
    [
        json.dumps({"sensordatavalues": [{"value_type": "voc_index", "value": 1}]}),
        f"[PAYLOAD] channel=sensors-connectivity encrypted=1 data={SECRET}",
        f"[PROTO] body={SECRET}",
        json.dumps({SECRET: {SECRET: {"value": 1}}}),
        SENSOR[:-2],
    ],
)
def test_non_sensor_inputs_do_not_prove_json(line):
    summary = UartSummary()
    summary.feed((line + "\n").encode())
    value = summary.finish()
    assert value["sensor_json_records"] == 0
    assert SECRET not in json.dumps(value)
    assert uart_check(value, "debug", "insight", None)["status"] == "FAIL"


def test_oversized_line_is_not_a_success_or_an_echo():
    summary = UartSummary()
    summary.feed((SECRET * 5000).encode() + b"\n")
    value = summary.finish()
    assert value["oversized_lines"] == 1
    assert SECRET not in json.dumps(value)
    assert uart_check(value, "release", "urban", "events")["status"] == "NOT_CHECKED"


@pytest.mark.parametrize(
    "line,mode,status",
    [
        ("", "off", "PASS"),
        ("[LOG] uptime=1 level=event subsystem=x event=y code=0\n", "off", "FAIL"),
        ("", "errors", "NOT_CHECKED"),
        ("[LOG] uptime=1 level=error subsystem=x event=y code=1\n", "errors", "PASS"),
        ("[LOG] uptime=1 level=event subsystem=x event=y code=0\n", "errors", "FAIL"),
        (SENSOR + "\n", "events", "FAIL"),
        (f"unknown {SECRET}\n", "events", "FAIL"),
        (HEALTH + "\n", "events", "FAIL"),
    ],
)
def test_release_windows_are_narrow_and_nonvacuous(line, mode, status):
    summary = UartSummary()
    summary.feed(line.encode())
    assert uart_check(summary.finish(), "release", "urban", mode)["status"] == status


def test_html_body_never_becomes_an_output_field():
    page = LogPage()
    page.feed(
        f"<input name='debug' value='{SECRET}'><div>{SECRET}</div>"
        f"<a href='/debug?lvl=5&token={SECRET}' aria-pressed='true'>secret</a>"
        f"<pre id='slog'>{BUILD}\n[LOG] status uptime=2 boot=3 password={SECRET}</pre>"
    )
    assert page.level is None and page.active is None
    assert page.fields["boot"] == 3
    assert SECRET not in json.dumps(page.fields)


@pytest.mark.parametrize(
    "before,after,status",
    [
        (State(ID, COMMIT, boot=1), State(ID, COMMIT, boot=2), "PASS"),
        (
            State(ID, COMMIT, boot=1, uptime=100),
            State(ID, COMMIT, boot=1, uptime=1),
            "FAIL",
        ),
        (State(ID, COMMIT, uptime=100), State(ID, COMMIT, uptime=2), "PASS"),
        (State(ID, COMMIT, uptime=100), State(ID, COMMIT, uptime=110), "FAIL"),
        (State(ID, COMMIT), State(ID, COMMIT), "NOT_CHECKED"),
        (State(ID, COMMIT, uptime=4294960), State(ID, COMMIT, uptime=2), "NOT_CHECKED"),
        (State(ID, COMMIT, boot=1), State("112233445566", COMMIT, boot=2), "FAIL"),
    ],
)
def test_reboot_requires_a_witness(before, after, status):
    reboot = reboot_check(before, after)
    assert reboot["status"] == status
    persistence = persistence_check(reboot, replace(after, level=3), 3)
    assert persistence["status"] == ("PASS" if status == "PASS" else "NOT_CHECKED")


def test_persistence_detects_mismatch_after_proven_boot():
    assert persistence_check({"status": "PASS"}, State(level=4), 3)["status"] == "FAIL"
    assert persistence_check({"status": "PASS"}, State(), 3)["status"] == "NOT_CHECKED"


class Firmware:
    def __init__(self, clock, *, profile="debug", witness=True, reboot=True):
        self.clock, self.profile, self.witness, self.reboot = (
            clock,
            profile,
            witness,
            reboot,
        )
        self.saved, self.level, self.boot, self.uptime = 3, 3, 1, 100
        self.fail_post = None
        self.calls = []

    @property
    def active(self):
        return (
            max(4, self.level)
            if self.profile == "debug"
            else min(3, 1 if self.level == 2 else self.level)
        )

    def build(self):
        return BUILD.replace("profile=debug", "profile=" + self.profile)

    def status(self):
        return self.build() + (
            f"\n[LOG] status uptime={self.uptime} boot={self.boot}"
            if self.witness
            else ""
        )

    def page(self):
        modes = [4, 5] if self.profile == "debug" else [0, 1, 3]
        return (
            f"<input name='debug' value='{self.level}'>"
            + "".join(
                f"<a href='/debug?lvl={n}' "
                f"aria-pressed='{str(n == self.active).lower()}'>mode</a>"
                for n in modes
            )
            + f"<pre id='slog'>{self.status()}\ncredential={SECRET}</pre>"
        )

    def request(self, path, budget, *, data=None):
        budget.remaining()
        self.clock.value += 0.05
        budget.remaining()
        self.calls.append((path, data))
        url = urlsplit(path)
        query = parse_qs(url.query)
        if data is not None:
            if self.fail_post:
                raise diag.CheckError(self.fail_post)
            if url.path == "/advanced":
                self.saved = int(parse_qs(data.decode())["debug"][0])
            if self.reboot:
                self.boot += 1
                self.uptime = 2
                self.level = self.saved
            return f"restarting {SECRET}".encode()
        if url.path == "/data.json":
            return json.dumps(
                {
                    "sensor_id": ID,
                    "software_version": "R-INS_test-testing+abc1234",
                    "wlanpwd": SECRET,
                    "www_password": SECRET,
                }
            ).encode()
        if url.path == "/serial":
            return (self.status() if query else "").encode()
        if "lvl" in query and "status" not in query:
            value = int(query["lvl"][0])
            if self.profile == "debug":
                if value >= 4:
                    self.level = value
            else:
                self.level = 1 if value == 2 else min(3, value)
        return self.page().encode()


def device(firmware):
    return diag.Device(firmware, ID, COMMIT, "insight", firmware.profile)


@pytest.mark.parametrize("profile", ["debug", "release"])
def test_http_cases_separate_results_and_restore(tmp_path, capsys, profile):
    clock = Clock()
    firmware = Firmware(clock, profile=profile)
    attempt = diag.Attempt(tmp_path, ["http-check", "--profile", profile])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    for key in (
        "readiness",
        "modes",
        "snapshot",
        "reboot",
        "persistence",
        "volatile",
        "restoration",
    ):
        assert outcomes[key]["status"] == "PASS", outcomes
    assert outcomes["browser"]["status"] == "NOT_CHECKED"
    assert firmware.saved == firmware.level == 3
    assert SECRET not in json.dumps(outcomes)
    assert SECRET not in capsys.readouterr().out
    for path in attempt.path.glob("*.json"):
        assert SECRET not in path.read_text()


def test_http_with_no_boot_witness_cannot_pass_persistence(tmp_path):
    clock = Clock()
    firmware = Firmware(clock, witness=False)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["readiness"]["status"] == "PASS"
    assert outcomes["reboot"]["status"] == "NOT_CHECKED"
    assert outcomes["persistence"]["status"] == "NOT_CHECKED"
    assert diag.exit_code(outcomes, ["persistence"]) == 2


@pytest.mark.parametrize("failure", ["http_error", "transport_error", SECRET])
def test_restart_exception_is_not_success(failure):
    clock = Clock()
    firmware = Firmware(clock, reboot=False)
    firmware.fail_post = failure
    dev = device(firmware)
    before = dev.observe(deadline(clock))
    limit = deadline(clock, 3)
    request, reboot, after = diag.restart_cycle(
        dev, before, lambda: dev.save(5, limit), limit
    )
    assert request["status"] != "PASS"
    assert reboot["status"] != "PASS"
    assert persistence_check(reboot, after or State(), 5)["status"] != "PASS"
    assert SECRET not in json.dumps([request, reboot])
    assert clock.value <= limit.end + 0.05


def test_response_loss_can_only_be_verified_by_a_new_boot():
    clock = Clock()
    firmware = Firmware(clock)
    dev = device(firmware)
    before = dev.observe(deadline(clock))
    limit = deadline(clock, 3)

    def action():
        firmware.boot += 1
        firmware.level = 5
        raise diag.CheckError("transport_error")

    request, reboot, after = diag.restart_cycle(dev, before, action, limit)
    assert request["status"] == "NOT_CHECKED"
    assert reboot["status"] == "PASS"
    assert persistence_check(reboot, after, 5)["status"] == "PASS"


def test_shared_deadline_counts_requests_and_retries(tmp_path):
    clock = Clock()

    class Failing:
        def observe(self, limit):
            limit.remaining()
            clock.value += min(0.7, limit.remaining())
            raise diag.CheckError(SECRET)

    attempt = diag.Attempt(tmp_path, [])
    limit = deadline(clock, 2)
    ready, state = diag.readiness(Failing(), limit, attempt.checkpoint)
    assert ready["status"] == "NOT_CHECKED" and state is None
    assert clock.value == 2
    assert SECRET not in (attempt.path / "state.json").read_text()


def test_transport_caps_entire_slow_request(monkeypatch, capsys):
    release = threading.Event()

    class Connection:
        def connect(self):
            pass

        sock = None
        closed = False

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return self

        status = 200

        def read(self, size):
            release.wait(0.5)
            return SECRET.encode()

        def close(self):
            self.closed = True
            release.set()

    connection = Connection()
    monkeypatch.setattr(diag.http.client, "HTTPConnection", lambda *a, **k: connection)
    start = time.monotonic()
    with pytest.raises(diag.CheckError, match="request_timeout"):
        diag.HttpTransport("127.0.0.1").request("/data.json", diag.Deadline(0.02))
    assert time.monotonic() - start < 0.3
    assert connection.closed
    assert SECRET not in capsys.readouterr().err


def test_transport_exception_text_and_http_body_are_not_exposed(monkeypatch, capsys):
    class Connection:
        def connect(self):
            pass

        def request(self, *args, **kwargs):
            raise OSError(SECRET)

        def close(self):
            pass

    monkeypatch.setattr(
        diag.http.client, "HTTPConnection", lambda *a, **k: Connection()
    )
    with pytest.raises(diag.CheckError) as exc:
        diag.HttpTransport("127.0.0.1").request("/data.json", diag.Deadline(1))
    assert str(exc.value) == "transport_error"
    assert SECRET not in capsys.readouterr().err


def test_exact_duration_command_hash_and_private_checkpoints(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("API_TOKEN", SECRET)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    args = ["uart-capture", "--duration-seconds", "330", "--cadence-seconds", "300"]
    attempt = diag.Attempt(tmp_path, args)
    attempt.phase("readiness", {"status": "PASS", "reason": "synthetic"})
    assert (attempt.path / "phases.json").exists()
    assert json.loads((attempt.path / "attempt.json").read_text())["exit_code"] is None
    attempt.finish(2)
    meta = json.loads((attempt.path / "attempt.json").read_text())
    assert meta["arguments"] == args and meta["replay_command"][-len(args) :] == args
    assert len(meta["hashes"]["diagnostics.py"]) == 64
    assert meta["safe_overrides"] == {"PYTHONDONTWRITEBYTECODE": "1"}
    assert meta["started_at"] and meta["finished_at"] and meta["exit_code"] == 2
    assert attempt.path.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in attempt.path.iterdir())
    assert SECRET not in capsys.readouterr().out
    assert SECRET not in "".join(
        path.read_text() for path in attempt.path.glob("*.json")
    )
    assert diag.Attempt(tmp_path, args).path != attempt.path


def test_synthetic_extraction_has_no_credential_output(tmp_path, capsys):
    source = tmp_path / "synthetic.log"
    source.write_text(
        json.dumps({"sensor_id": ID, "ip": "192.168.1.2", "password": SECRET})
        + "\n"
        + SENSOR
        + "\n"
    )
    output = tmp_path / "output"
    code = diag.main(["extract", "--input", str(source), "--output-root", str(output)])
    assert code == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    summaries = [
        json.loads(path.read_text()) for path in output.glob("*/extracted.json")
    ]
    assert summaries[0]["fields"]["device_id"] == ID
    assert summaries[0]["sensor_json_records"] == 5
    assert SECRET not in "".join(path.read_text() for path in output.rglob("*.json"))


def test_input_and_checkpoint_errors_are_sanitized(tmp_path, capsys, monkeypatch):
    with pytest.raises(SystemExit):
        diag.main(["--invalid", SECRET])

    def failed_write(*args, **kwargs):
        raise OSError(SECRET)

    monkeypatch.setattr(diag.Attempt, "write", failed_write)
    assert (
        diag.main(
            [
                "extract",
                "--input",
                str(tmp_path / "missing"),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_cadence_validation_happens_before_any_live_io(tmp_path, capsys):
    code = diag.main(
        [
            "uart-capture",
            "--device-id",
            ID,
            "--firmware-commit",
            COMMIT,
            "--model",
            "insight",
            "--profile",
            "debug",
            "--port",
            "unused",
            "--duration-seconds",
            "150",
            "--cadence-seconds",
            "300",
        ]
    )
    assert code == 2
    assert "capture_shorter_than_cadence_margin" in capsys.readouterr().err


def test_capture_saves_startup_and_bounded_window_without_live_device(tmp_path):
    clock = Clock()
    chunks = [
        BUILD.encode() + b"\n",
        (HEALTH + "\n" + SENSOR + "\n").encode(),
        (json.dumps({"password": SECRET}) + "\n").encode(),
    ]

    class Stream:
        timeout = 0.2

        def read(self, size):
            clock.value += 0.4
            return chunks.pop(0) if chunks else b""

    attempt = diag.Attempt(tmp_path, ["uart-capture", "--duration-seconds", "2"])
    startup, window = diag.capture_uart(
        Stream(), 0.5, 2, attempt, clock=clock.now, sleep=clock.sleep
    )
    assert startup["fields"]["commit"] == COMMIT
    assert window["sensor_json_records"] == 5
    assert SECRET not in json.dumps(window)
    assert SECRET.encode() in (attempt.path / "serial.log").read_bytes()
    assert (attempt.path / "serial.log").stat().st_mode & 0o777 == 0o600
    assert clock.value <= 2.9


def test_missing_identity_is_not_a_reboot_witness():
    assert reboot_check(State(boot=1), State(boot=2))["status"] == "NOT_CHECKED"


def test_conflicting_uart_identity_is_retained_as_safe_field_names():
    summary = UartSummary()
    summary.feed((BUILD + "\n" + BUILD.replace("abc1234", "def5678") + "\n").encode())
    value = summary.finish()
    assert value["identity_conflicts"] == ["commit"]
    assert SECRET not in json.dumps(value)


def test_aggregate_never_hides_failed_command_or_missing_predicate():
    outcomes = {"readiness": {"status": "PASS"}, "restart_request": {"status": "FAIL"}}
    assert diag.exit_code(outcomes, ["readiness"]) == 1
    assert (
        diag.exit_code({"readiness": {"status": "PASS"}}, ["readiness", "reboot"]) == 2
    )
    assert (
        diag.exit_code(
            {"readiness": {"status": "PASS"}, "browser": {"status": "NOT_CHECKED"}},
            ["readiness"],
        )
        == 0
    )


def test_http_rejects_identity_before_any_mutation(tmp_path):
    clock = Clock()

    class Other(Firmware):
        def request(self, path, limit, *, data=None):
            body = super().request(path, limit, data=data)
            return body.replace(ID.encode(), b"112233445566")

    firmware = Other(clock)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["readiness"]["status"] == "FAIL"
    assert not any(body is not None or "lvl=" in path for path, body in firmware.calls)


def test_failed_http_restart_is_visible_and_not_persistence_pass(tmp_path):
    clock = Clock()
    firmware = Firmware(clock)
    firmware.fail_post = "http_error"
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["restart_request"]["status"] == "FAIL"
    assert outcomes["persistence"]["status"] == "NOT_CHECKED"
    assert diag.exit_code(outcomes, ["readiness", "persistence"]) == 1


def test_snapshot_must_not_change_active_mode(tmp_path):
    clock = Clock()

    class BrokenSnapshot(Firmware):
        def request(self, path, limit, *, data=None):
            if "status=1&lvl=5" in path:
                self.level = 5
            return super().request(path, limit, data=data)

    firmware = BrokenSnapshot(clock)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["snapshot"]["status"] == "FAIL"
    assert firmware.saved == firmware.level == 3


def test_unavailable_profile_keeps_readiness_unverified(tmp_path):
    clock = Clock()
    firmware = Firmware(clock)
    firmware.build = lambda: ""
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(device(firmware), deadline(clock), attempt)
    assert outcomes["readiness"]["status"] == "NOT_CHECKED"
    assert outcomes["browser"]["status"] == "NOT_CHECKED"


def test_no_restore_budget_is_created_by_retry(tmp_path):
    clock = Clock()
    firmware = Firmware(clock, reboot=False)
    attempt = diag.Attempt(tmp_path, [])
    limit = deadline(clock, 8)
    outcomes = diag.run_http(device(firmware), limit, attempt, exercise=True, reserve=3)
    assert outcomes["reboot"]["status"] != "PASS"
    assert outcomes["restoration"]["status"] != "PASS"
    assert clock.value <= limit.end + 0.05


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (500, SECRET.encode(), "http_error"),
        (200, (SECRET * 5000).encode(), "response_too_large"),
    ],
)
def test_http_errors_and_oversized_body_have_fixed_reason_codes(
    monkeypatch, capsys, status, body, reason
):
    class Connection:
        def connect(self):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return self

        def read(self, size):
            return body

        def close(self):
            pass

    connection = Connection()
    connection.status = status
    monkeypatch.setattr(diag.http.client, "HTTPConnection", lambda *a, **k: connection)
    with pytest.raises(diag.CheckError) as exc:
        diag.HttpTransport("127.0.0.1").request("/data.json", diag.Deadline(1))
    assert str(exc.value) == reason
    assert SECRET not in capsys.readouterr().err


def test_live_command_rejects_tmp_output_before_open_or_request(tmp_path, capsys):
    code = diag.main(
        [
            "http-check",
            "--host",
            "127.0.0.1",
            "--device-id",
            ID,
            "--firmware-commit",
            COMMIT,
            "--model",
            "insight",
            "--profile",
            "debug",
            "--output-root",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "durable_output_required" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_synthetic_cli_http_failure_has_no_credential_in_any_output(
    tmp_path, capsys, monkeypatch
):
    # Only tests may use pytest's temporary directory as live-command output.
    monkeypatch.setattr(diag.tempfile, "gettempdir", lambda: "/not-a-test-output")
    clock = Clock()
    firmware = Firmware(clock, witness=False)
    monkeypatch.setattr(diag, "HttpTransport", lambda host: firmware)
    original = diag.Deadline
    monkeypatch.setattr(
        diag,
        "Deadline",
        lambda seconds, **kw: original(
            seconds, **{"clock": clock.now, "sleep": clock.sleep, **kw}
        ),
    )
    code = diag.main(
        [
            "http-check",
            "--host",
            "127.0.0.1",
            "--device-id",
            ID,
            "--firmware-commit",
            COMMIT,
            "--model",
            "insight",
            "--profile",
            "debug",
            "--exercise",
            "--output-root",
            str(tmp_path),
        ]
    )
    assert code == 2
    http = next(tmp_path.glob("*/http.json"))
    outcomes = json.loads(http.read_text())
    assert outcomes["readiness"]["status"] == "PASS"
    assert outcomes["reboot"]["status"] == "NOT_CHECKED"
    assert outcomes["persistence"]["status"] == "NOT_CHECKED"
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    for path in tmp_path.rglob("*.json"):
        assert SECRET not in path.read_text()


def test_stable_version_uses_explicit_build_commit(tmp_path):
    clock = Clock()

    class Stable(Firmware):
        def request(self, path, budget, *, data=None):
            body = super().request(path, budget, data=data)
            if path == "/data.json":
                body = body.replace(b"R-INS_test-testing+abc1234", b"R-INS_test")
            return body

    firmware = Stable(clock)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(device(firmware), deadline(clock), attempt)
    assert outcomes["readiness"]["status"] == "PASS"


def test_restore_rejection_is_an_explicit_failure(tmp_path):
    clock = Clock()

    class RejectRestore(Firmware):
        def request(self, path, budget, *, data=None):
            if path == "/advanced" and data == b"debug=3":
                raise diag.CheckError("http_error")
            return super().request(path, budget, data=data)

    firmware = RejectRestore(clock)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["restoration"]["status"] == "FAIL"
    assert diag.exit_code(outcomes, ["readiness", "restoration"]) == 1


def test_capture_exact_byte_window_and_digest(tmp_path):
    import hashlib

    clock = Clock()
    chunks = [BUILD.encode() + b"\n", (HEALTH + "\n" + SENSOR + "\n").encode()]

    class Stream:
        timeout = 0.2

        def read(self, size):
            clock.value += 0.4
            return chunks.pop(0) if chunks else b""

    attempt = diag.Attempt(tmp_path, ["uart-capture", "--duration-seconds", "2"])
    _, summary = diag.capture_uart(
        Stream(), 0.5, 2, attempt, clock=clock.now, sleep=clock.sleep
    )
    raw = (attempt.path / "serial.log").read_bytes()
    assert attempt.metadata["serial_sha256"] == hashlib.sha256(raw).hexdigest()
    window = summary["window"]
    replay = UartSummary()
    replay.feed(raw[window["start_byte"] : window["end_byte"]])
    assert replay.finish()["sensor_json_records"] == summary["sensor_json_records"]
    assert window["duration_seconds"] == 2 and window["startup_seconds"] == 0.5


def test_auth_unavailable_is_explicit_without_readiness_polling(tmp_path):
    clock = Clock()

    class AuthRequired:
        def observe(self, limit):
            raise diag.CheckError("http_auth_required")

    attempt = diag.Attempt(tmp_path, [])
    ready, state = diag.readiness(AuthRequired(), deadline(clock), attempt.checkpoint)
    assert ready == {"status": "NOT_CHECKED", "reason": "http_auth_not_configured"}
    assert state is None and clock.value == 0


def test_interrupt_closes_pending_connection_before_propagating(monkeypatch):
    release, done = threading.Event(), threading.Event()
    calls = []

    class Connection:
        sock = None

        def connect(self):
            release.wait(0.5)
            done.set()

        def request(self, *args, **kwargs):
            calls.append("unexpected_request_after_cancel")

        def close(self):
            release.set()

    connection = Connection()
    monkeypatch.setattr(diag.http.client, "HTTPConnection", lambda *a, **k: connection)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(diag.queue.Queue, "get", interrupt)
    with pytest.raises(KeyboardInterrupt):
        diag.HttpTransport("127.0.0.1").request("/data.json", diag.Deadline(1))
    assert done.wait(0.2)
    assert calls == []


def test_restore_target_is_loaded_saved_value_not_initial_ram(tmp_path):
    clock = Clock()
    firmware = Firmware(clock, profile="release")
    firmware.saved = 2
    firmware.level = 3
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["baseline_reboot"]["status"] == "PASS"
    assert outcomes["restoration"]["status"] == "PASS"
    assert firmware.saved == firmware.level == 2


def test_missing_baseline_witness_never_saves_configuration(tmp_path):
    clock = Clock()
    firmware = Firmware(clock, witness=False)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, exercise=True, reserve=30
    )
    assert outcomes["baseline_reboot"]["status"] == "NOT_CHECKED"
    assert not any(
        path == "/advanced" and body is not None for path, body in firmware.calls
    )


@pytest.mark.parametrize(
    "profile,level,active", [("release", 5, 3), ("release", 2, 1), ("debug", 0, 4)]
)
def test_live_mode_change_is_separate_from_persistence(
    tmp_path, profile, level, active
):
    clock = Clock()
    firmware = Firmware(clock, profile=profile)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(
        device(firmware), deadline(clock), attempt, set_level=level
    )
    assert outcomes["mode_change"]["status"] == "PASS"
    assert firmware.active == active and firmware.saved == 3
    assert outcomes["persistence"]["status"] == "NOT_CHECKED"
    assert not any(body is not None for _, body in firmware.calls)


def test_boot_and_persistence_witness_values_are_preserved():
    check = reboot_check(
        State(ID, COMMIT, boot=1, uptime=100), State(ID, COMMIT, boot=2, uptime=2)
    )
    assert check["witness"] == {
        "before_boot": 1,
        "after_boot": 2,
        "before_uptime": 100,
        "after_uptime": 2,
    }
    saved = persistence_check(check, State(level=3), 3)
    assert saved["expected_level"] == saved["observed_level"] == 3


@pytest.mark.parametrize(
    "line",
    [
        f"[LOG] uptime=1 level=error password={SECRET}",
        f"[LOG] uptime=1 level=error subsystem=x event=y code=1 signature={SECRET}",
        BUILD + f" password={SECRET}",
    ],
)
def test_release_rejects_extra_operational_fields_without_echo(line):
    summary = UartSummary()
    summary.feed((line + "\n").encode())
    value = summary.finish()
    assert uart_check(value, "release", "urban", "events")["status"] == "FAIL"
    assert SECRET not in json.dumps(value)


def test_documented_synthetic_fixture_roundtrip(tmp_path, capsys):
    source = Path(__file__).parent / "fixtures/diagnostic_uart.log"
    output = tmp_path / "fixture-output"
    assert (
        diag.main(["extract", "--input", str(source), "--output-root", str(output)])
        == 0
    )
    extracted = json.loads(next(output.glob("*/extracted.json")).read_text())
    assert extracted["sensor_json_records"] == 6
    assert extracted["fields"]["device_id"] == ID
    assert SECRET not in json.dumps(extracted)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_cli_serial_error_never_echoes_exception(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(diag.tempfile, "gettempdir", lambda: "/not-a-test-output")
    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda: [SimpleNamespace(serial_number=ID, device="unused")],
    )

    def refused(*args, **kwargs):
        raise OSError(SECRET)

    monkeypatch.setattr(serial, "Serial", refused)
    code = diag.main(
        [
            "uart-capture",
            "--port",
            "unused",
            "--device-id",
            ID,
            "--firmware-commit",
            COMMIT,
            "--model",
            "insight",
            "--profile",
            "debug",
            "--duration-seconds",
            "330",
            "--cadence-seconds",
            "300",
            "--output-root",
            str(tmp_path),
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert SECRET not in "".join(path.read_text() for path in tmp_path.rglob("*.json"))


def test_conflicting_http_actions_do_not_start_io(capsys):
    with pytest.raises(SystemExit):
        diag.main(
            [
                "http-check",
                "--host",
                "127.0.0.1",
                "--device-id",
                ID,
                "--firmware-commit",
                COMMIT,
                "--model",
                "insight",
                "--profile",
                "debug",
                "--exercise",
                "--set-level",
                "3",
            ]
        )
    assert "invalid_arguments" in capsys.readouterr().err


def test_missing_stable_build_is_unverified_not_wrong_firmware(tmp_path):
    clock = Clock()

    class MissingBuild(Firmware):
        def build(self):
            return ""

        def request(self, path, budget, *, data=None):
            body = super().request(path, budget, data=data)
            if path == "/data.json":
                body = body.replace(b"R-INS_test-testing+abc1234", b"R-INS_test")
            return body

    firmware = MissingBuild(clock)
    attempt = diag.Attempt(tmp_path, [])
    outcomes = diag.run_http(device(firmware), deadline(clock), attempt)
    assert outcomes["readiness"] == {
        "status": "NOT_CHECKED",
        "reason": "build_witness_unavailable",
    }
