"""Bounded diagnostic operations; explicit live subcommands require device approval.

Use ``python -B -m altruist_tester.diagnostics --help``. No device discovery,
network access or configuration change occurs on import, help or extraction.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester import diagnostic_checks as checks
from altruist_tester.diagnostic_checks import (
    LogPage,
    State,
    UartSummary,
    commit_id,
    device_id,
    persistence_check,
    reboot_check,
    result,
    safe_fields,
    uart_check,
)


class CheckError(Exception):
    """Only fixed internal reason codes may cross the output boundary."""

    CODES = frozenset(
        {
            "deadline_exceeded",
            "http_error",
            "http_auth_required",
            "response_too_large",
            "transport_error",
            "request_timeout",
            "identity_mismatch",
            "firmware_mismatch",
            "profile_mismatch",
            "build_witness_unavailable",
            "mode_mismatch",
            "invalid_arguments",
            "durable_output_required",
            "capture_shorter_than_cadence_margin",
            "usb_identity_mismatch",
        }
    )

    def __init__(self, code):
        super().__init__(code if code in self.CODES else "operation_failed")


def utc_now():
    return datetime.now(UTC).isoformat()


class Deadline:
    def __init__(self, seconds, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleeper = clock, sleep
        self.end = clock() + seconds

    def remaining(self):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise CheckError("deadline_exceeded")
        return remaining

    def sleep(self, seconds):
        self.sleeper(min(seconds, self.remaining()))

    def reserved(self, seconds):
        child = Deadline(0, clock=self.clock, sleep=self.sleeper)
        child.end = self.end - seconds
        return child


class Attempt:
    """One private, durable attempt; never replace an earlier run directory."""

    def __init__(self, root: Path, argv: list[str], *, inputs=None):
        self.id = uuid.uuid4().hex
        self.path = root / self.id
        self.path.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.metadata = {
            "attempt_id": self.id,
            "started_at": utc_now(),
            "finished_at": None,
            "exit_code": None,
            "arguments": argv,
            "inputs": inputs or {},
            "replay_command": [
                sys.executable,
                "-B",
                "-m",
                "altruist_tester.diagnostics",
                *argv,
            ],
            "hashes": {
                "diagnostics.py": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "diagnostic_checks.py": hashlib.sha256(
                    Path(checks.__file__).read_bytes()
                ).hexdigest(),
                "sensor_values.py": hashlib.sha256(
                    Path(
                        sys.modules["altruist_tester.parsers.sensor_values"].__file__
                    ).read_bytes()
                ).hexdigest(),
            },
            "safe_overrides": {"PYTHONDONTWRITEBYTECODE": "1"}
            if os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
            else {},
        }
        self.phases = {}
        self.write("attempt.json", self.metadata)
        self.checkpoint("started", {})

    def write(self, name, value):
        # Files in this newly owned attempt are replaced atomically, not evidence
        # from any previous attempt. fsync before exposing the new checkpoint.
        path = self.path / name
        temporary = path.with_suffix(path.suffix + ".new")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def phase(self, name, value):
        self.phases[name] = value
        self.write("phases.json", self.phases)
        self.checkpoint(name, {"status": value["status"]})

    def checkpoint(self, phase, progress):
        value = {
            "attempt_id": self.id,
            "phase": phase,
            "updated_at": utc_now(),
            **progress,
        }
        self.write("state.json", value)
        print(json.dumps(value), flush=True)

    def finish(self, exit_code):
        self.metadata.update(finished_at=utc_now(), exit_code=exit_code)
        self.write("attempt.json", self.metadata)
        self.checkpoint("finished", {"exit_code": exit_code})


class HttpTransport:
    def __init__(self, host):
        address = ipaddress.ip_address(host)
        self.host = str(address)

    def request(self, path, deadline, *, data=None):
        # Socket timeouts alone do not bound slow headers/body. Bound the whole
        # request in one daemon worker and close its connection on expiry.
        budget = min(5.0, deadline.remaining())
        connection = http.client.HTTPConnection(self.host, timeout=budget)
        responses = queue.Queue(maxsize=1)
        cancelled = threading.Event()

        def cancel():
            cancelled.set()
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                connection.close()
            except Exception:
                pass

        def worker():
            try:
                connection.connect()
                if cancelled.is_set():
                    return
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                connection.request(
                    "POST" if data is not None else "GET",
                    path,
                    body=data,
                    headers=headers,
                )
                response = connection.getresponse()
                body = response.read(checks.MAX_BODY + 1)
                if response.status in {401, 403}:
                    responses.put(("http_auth_required", None))
                elif not 200 <= response.status < 300:
                    responses.put(("http_error", None))
                elif len(body) > checks.MAX_BODY:
                    responses.put(("response_too_large", None))
                else:
                    responses.put((None, body))
            except Exception:
                # Exception text/URL/reason/body may contain credentials.
                responses.put(("transport_error", None))
            finally:
                try:
                    connection.close()
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        try:
            reason, body = responses.get(timeout=budget)
        except queue.Empty:
            cancel()
            raise CheckError("request_timeout") from None
        except BaseException:
            cancel()
            raise
        if reason:
            raise CheckError(reason)
        deadline.remaining()
        return body


class Device:
    def __init__(self, transport, identity, commit, model, profile):
        self.transport = transport
        self.identity, self.commit = identity, commit
        self.model, self.profile = model, profile

    def request(self, path, deadline, *, data=None):
        return self.transport.request(path, deadline, data=data)

    def page(self, path, deadline):
        page = LogPage()
        page.feed(self.request(path, deadline).decode("utf-8", errors="replace"))
        return page

    def observe(self, deadline):
        fields = safe_fields(
            self.request("/data.json", deadline).decode("utf-8", errors="replace")
        )
        if fields.get("device_id") != self.identity:
            raise CheckError("identity_mismatch")
        if (
            fields.get("commit") not in {None, self.commit}
            or fields.get("model") != self.model
        ):
            raise CheckError("firmware_mismatch")
        # Drain old records before requesting the new snapshot.
        self.request("/serial", deadline)
        status = self.request("/serial?status=1", deadline).decode(
            "utf-8", errors="replace"
        )
        for line in status.splitlines():
            fields.update(safe_fields(line))
        if fields.get("device_id") != self.identity:
            raise CheckError("identity_mismatch")
        if fields.get("commit") is None:
            raise CheckError("build_witness_unavailable")
        if fields.get("commit") != self.commit or fields.get("model") != self.model:
            raise CheckError("firmware_mismatch")
        if fields.get("profile") not in {None, self.profile}:
            raise CheckError("profile_mismatch")
        page = self.page("/advanced", deadline)
        return State(
            identity=self.identity,
            commit=self.commit,
            model=self.model,
            profile=fields.get("profile"),
            boot=fields.get("boot"),
            uptime=fields.get("uptime"),
            level=page.level,
        )

    def save(self, level, deadline):
        return self.request(
            "/advanced",
            deadline,
            data=urllib.parse.urlencode({"debug": level}).encode(),
        )


def readiness(device, deadline, heartbeat):
    next_progress = deadline.clock()
    while True:
        try:
            state = device.observe(deadline)
            if state.profile is None:
                return result("NOT_CHECKED", "profile_witness_unavailable"), state
            return result("PASS", "http_identity_and_profile_verified"), state
        except CheckError as exc:
            reason = str(exc)
            if reason == "build_witness_unavailable":
                return result("NOT_CHECKED", reason), None
            if reason == "http_auth_required":
                return result("NOT_CHECKED", "http_auth_not_configured"), None
            if reason == "response_too_large":
                return result("FAIL", "response_too_large"), None
            if reason in {"identity_mismatch", "firmware_mismatch", "profile_mismatch"}:
                return result("FAIL", reason), None
            try:
                if deadline.clock() >= next_progress:
                    heartbeat(
                        "readiness_wait",
                        {"remaining_seconds": round(deadline.remaining(), 1)},
                    )
                    next_progress = deadline.clock() + 10
                deadline.sleep(1)
            except CheckError:
                return result("NOT_CHECKED", "http_readiness_deadline"), None


def restart_cycle(device, before, action, deadline, heartbeat=None):
    request = result("PASS", "restart_response_received")
    try:
        action()
    except CheckError as exc:
        if str(exc) in {"http_error", "http_auth_required"}:
            return (
                result("FAIL", "restart_http_error"),
                result("NOT_CHECKED", "restart_rejected"),
                None,
            )
        request = result("NOT_CHECKED", "restart_response_unavailable")
    next_progress = deadline.clock()
    after = None
    witness = result("NOT_CHECKED", "no_post_restart_observation")
    while True:
        try:
            after = device.observe(deadline)
            witness = reboot_check(before, after)
            if witness["status"] in {"PASS", "NOT_CHECKED"}:
                return request, witness, after
            if witness["reason"] == "identity_or_version_changed":
                return request, witness, after
        except CheckError as exc:
            if str(exc) in {
                "identity_mismatch",
                "firmware_mismatch",
                "profile_mismatch",
            }:
                return request, result("FAIL", str(exc)), after
        try:
            if heartbeat and deadline.clock() >= next_progress:
                heartbeat(
                    "reboot_wait", {"remaining_seconds": round(deadline.remaining(), 1)}
                )
                next_progress = deadline.clock() + 10
            deadline.sleep(1)
        except CheckError:
            return request, witness, after


def run_http(device, deadline, attempt, *, exercise=False, reserve=60, set_level=None):
    names = (
        "readiness",
        "baseline_reboot",
        "modes",
        "snapshot",
        "restart_request",
        "reboot",
        "persistence",
        "volatile",
        "restoration",
        "browser",
    )
    outcomes = {name: result("NOT_CHECKED", "not_requested") for name in names}
    outcomes["browser"] = result("NOT_CHECKED", "browser_not_run")
    work = deadline.reserved(reserve) if exercise else deadline
    outcomes["readiness"], initial = readiness(device, work, attempt.checkpoint)
    attempt.phase("readiness", outcomes["readiness"])
    if outcomes["readiness"]["status"] != "PASS":
        return outcomes
    if set_level is not None:
        try:
            page = device.page(f"/debug?lvl={set_level}", deadline)
            if device.profile == "release":
                expected = 1 if set_level == 2 else min(3, set_level)
            else:
                expected = set_level if set_level >= 4 else max(4, initial.level or 0)
            outcomes["mode_change"] = (
                result("PASS", "runtime_level_verified")
                if page.active == expected
                else result("FAIL", "mode_mismatch")
            )
            outcomes["mode_change"]["prior_runtime_level"] = initial.level
            outcomes["mode_change"].update(
                requested_level=set_level,
                expected_active_level=expected,
                observed_active_level=page.active,
            )
        except CheckError as exc:
            outcomes["mode_change"] = result("FAIL", str(exc))
        attempt.phase("mode_change", outcomes["mode_change"])
        return outcomes
    if not exercise:
        return outcomes
    # The initial HTML field is RAM state, not evidence of the persisted value.
    # Reload it without saving anything before selecting a restoration target.
    req, baseline, loaded = restart_cycle(
        device,
        initial,
        lambda: device.request("/restart", work, data=b""),
        work,
        attempt.checkpoint,
    )
    outcomes.update(
        restart_request=req,
        baseline_restart_request=req,
        baseline_reboot=baseline,
        reboot=baseline,
    )
    attempt.phase("baseline_restart_request", req)
    attempt.phase("baseline_reboot", baseline)
    if baseline["status"] != "PASS" or loaded is None:
        outcomes["persistence"] = result("NOT_CHECKED", "saved_baseline_not_verified")
        attempt.phase("persistence", outcomes["persistence"])
        return outcomes
    initial = loaded
    if initial.level is None:
        outcomes["persistence"] = result("NOT_CHECKED", "initial_level_unavailable")
        attempt.phase("persistence", outcomes["persistence"])
        return outcomes
    outcomes["saved_baseline"] = {
        **result("PASS", "loaded_from_verified_boot"),
        "level": initial.level,
    }
    attempt.phase("saved_baseline", outcomes["saved_baseline"])
    dirty = False
    try:
        levels = [0, 1, 3, 5, 2] if device.profile == "release" else [4, 5, 0]
        expected = [0, 1, 3, 3, 1] if device.profile == "release" else [4, 5, 5]
        observations = []
        for level, active in zip(levels, expected, strict=True):
            dirty = True
            page = device.page(f"/debug?lvl={level}", work)
            observations.append(
                {
                    "requested_level": level,
                    "active_level": page.active,
                    "controls": sorted(page.controls),
                }
            )
            allowed = {0, 1, 3} if device.profile == "release" else {4, 5}
            if set(page.controls) != allowed or page.active != active:
                raise CheckError("mode_mismatch")
        outcomes["modes"] = {
            **result("PASS", "requested_modes_verified"),
            "observations": observations,
        }
        attempt.phase("modes", outcomes["modes"])
        target = 0 if device.profile == "release" else 4
        device.page(f"/debug?lvl={target}", work)
        snapshot = device.page("/debug?status=1&lvl=5", work)
        if snapshot.active != target:
            outcomes["snapshot"] = result("FAIL", "snapshot_changed_mode")
        elif snapshot.fields.get("profile") == device.profile:
            outcomes["snapshot"] = result("PASS", "snapshot_preserved_mode")
        else:
            outcomes["snapshot"] = result("NOT_CHECKED", "snapshot_witness_unavailable")
        outcomes["snapshot"].update(
            before_active_level=target, after_active_level=snapshot.active
        )
        attempt.phase("snapshot", outcomes["snapshot"])
        saved, volatile = (1, 3) if device.profile == "release" else (5, 4)
        before = device.observe(work)
        req, reboot, after = restart_cycle(
            device, before, lambda: device.save(saved, work), work, attempt.checkpoint
        )
        outcomes.update(restart_request=req, reboot=reboot)
        outcomes["persistence"] = persistence_check(reboot, after or State(), saved)
        for name in ("restart_request", "reboot", "persistence"):
            attempt.phase(name, outcomes[name])
        if outcomes["persistence"]["status"] == "PASS":
            device.page(f"/debug?lvl={volatile}", work)
            before = device.observe(work)
            req, reboot, after = restart_cycle(
                device,
                before,
                lambda: device.request("/restart", work, data=b""),
                work,
                attempt.checkpoint,
            )
            outcomes["volatile"] = (
                result("FAIL", "restart_rejected")
                if req["status"] == "FAIL"
                else persistence_check(reboot, after or State(), saved)
            )
            outcomes["volatile"]["restart_request"] = req
            outcomes["volatile"]["reboot"] = reboot
            attempt.phase("volatile", outcomes["volatile"])
    except CheckError as exc:
        outcomes["operation"] = result("FAIL", str(exc))
        attempt.phase("operation", outcomes["operation"])
    finally:
        if dirty:
            try:
                before = device.observe(deadline)
                req, reboot, after = restart_cycle(
                    device,
                    before,
                    lambda: device.save(initial.level, deadline),
                    deadline,
                    attempt.checkpoint,
                )
                outcomes["restoration"] = (
                    result("FAIL", "restore_rejected")
                    if req["status"] == "FAIL"
                    else persistence_check(reboot, after or State(), initial.level)
                )
                outcomes["restoration"]["restart_request"] = req
                outcomes["restoration"]["reboot"] = reboot
            except CheckError:
                outcomes["restoration"] = result(
                    "NOT_CHECKED", "restoration_deadline_or_transport"
                )
            attempt.phase("restoration", outcomes["restoration"])
    return outcomes


def capture_uart(
    stream, startup, duration, attempt, *, clock=time.monotonic, sleep=time.sleep
):
    start = clock()
    observe_at, end = start + startup, start + startup + duration
    startup_summary, window = UartSummary(), UartSummary()
    last_progress = start
    digest = hashlib.sha256()
    window_start = window_end = None
    fd = os.open(
        attempt.path / "serial.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(fd, "wb") as raw:
        attempt.checkpoint("startup", {"remaining_seconds": round(end - start, 1)})
        startup_saved = False
        while clock() < end:
            stream.timeout = min(0.2, max(0.001, end - clock()))
            chunk = stream.read(8192)
            now = clock()
            raw.write(chunk)
            digest.update(chunk)
            if now < observe_at:
                startup_summary.feed(chunk)
            else:
                if not startup_saved:
                    raw.flush()
                    os.fsync(raw.fileno())
                    attempt.write("startup.json", startup_summary.finish())
                    attempt.phase("startup", result("PASS", "startup_window_saved"))
                    startup_saved = True
                    window_start = raw.tell() - len(chunk)
                if now <= end:
                    window.feed(chunk)
                    window_end = raw.tell()
            if now - last_progress >= 10:
                raw.flush()
                os.fsync(raw.fileno())
                attempt.checkpoint(
                    "observation",
                    {
                        "remaining_seconds": round(max(0, end - now), 1),
                        "received_bytes": startup_summary.bytes + window.bytes,
                    },
                )
                last_progress = now
            if not chunk:
                sleep(min(0.05, max(0, end - clock())))
        raw.flush()
        os.fsync(raw.fileno())
    start_data, data = startup_summary.finish(), window.finish()
    attempt.write("startup.json", start_data)
    data["window"] = {
        "start_byte": window_start,
        "end_byte": window_end,
        "startup_seconds": startup,
        "duration_seconds": duration,
        "read_timeout_seconds": 0.2,
    }
    attempt.metadata["serial_sha256"] = digest.hexdigest()
    attempt.write("uart.json", data)
    return start_data, data


def exit_code(outcomes, requested):
    statuses = [
        outcomes.get(key, {"status": "NOT_CHECKED"})["status"] for key in requested
    ]
    if any(value["status"] == "FAIL" for value in outcomes.values()):
        return 1
    return 0 if statuses and all(value == "PASS" for value in statuses) else 2


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid input values, which may be secrets.
        print(json.dumps(result("FAIL", "invalid_arguments")), file=sys.stderr)
        raise SystemExit(2)


def parser():
    main = SafeParser(description=__doc__)
    commands = main.add_subparsers(dest="command", required=True)
    for command in ("extract", "uart-capture", "http-check"):
        sub = commands.add_parser(command)
        sub.add_argument(
            "--output-root",
            type=Path,
            default=Path(__file__).resolve().parents[1] / "runs/diagnostics",
        )
        if command == "extract":
            sub.add_argument("--input", type=Path, required=True)
        else:
            sub.add_argument("--device-id", required=True)
            sub.add_argument("--firmware-commit", required=True)
            sub.add_argument("--model", choices=("urban", "insight"), required=True)
            sub.add_argument("--profile", choices=("debug", "release"), required=True)
        if command == "http-check":
            sub.add_argument("--host", required=True)
            actions = sub.add_mutually_exclusive_group()
            actions.add_argument(
                "--set-level",
                type=int,
                choices=range(6),
                help="Change only the live log mode; requires device approval",
            )
            actions.add_argument(
                "--exercise",
                action="store_true",
                help="Change log modes and save/restart; requires device approval",
            )
            sub.add_argument("--timeout-seconds", type=float, default=180)
            sub.add_argument("--restore-reserve-seconds", type=float, default=60)
        if command == "uart-capture":
            sub.add_argument("--port", required=True)
            sub.add_argument("--duration-seconds", type=float, required=True)
            sub.add_argument("--startup-seconds", type=float, default=30)
            sub.add_argument("--cadence-seconds", type=float, required=True)
            sub.add_argument("--margin-seconds", type=float, default=30)
            sub.add_argument("--mode", choices=("off", "errors", "events"))
    return main


def validate(args):
    for key, value in vars(args).items():
        if key.endswith("seconds") and (not math.isfinite(value) or value < 0):
            raise CheckError("invalid_arguments")
    if args.command == "extract":
        return
    args.device_id = device_id(args.device_id)
    args.firmware_commit = commit_id(args.firmware_commit)
    if not args.device_id or not args.firmware_commit or len(args.firmware_commit) != 7:
        raise CheckError("invalid_arguments")
    if args.output_root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise CheckError("durable_output_required")
    if args.command == "http-check":
        ipaddress.ip_address(args.host)
        if args.timeout_seconds <= 0 or (
            args.exercise
            and not 0 < args.restore_reserve_seconds < args.timeout_seconds
        ):
            raise CheckError("invalid_arguments")
    else:
        if args.duration_seconds <= 0:
            raise CheckError("invalid_arguments")
        if args.profile == "release" and args.mode is None:
            raise CheckError("invalid_arguments")
        if args.profile == "debug" and (
            args.cadence_seconds <= 0
            or args.duration_seconds < args.cadence_seconds + args.margin_seconds
        ):
            raise CheckError("capture_shorter_than_cadence_margin")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(argv)
    attempt = None
    code = 2
    try:
        validate(args)
        inputs = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        }
        attempt = Attempt(args.output_root, argv, inputs=inputs)
        if args.command == "extract":
            summary = UartSummary()
            digest = hashlib.sha256()
            with args.input.open("rb") as stream:
                while chunk := stream.read(8192):
                    summary.feed(chunk)
                    digest.update(chunk)
            attempt.metadata["input_sha256"] = digest.hexdigest()
            attempt.write("extracted.json", summary.finish())
            attempt.phase("extraction", result("PASS", "safe_fields_saved"))
            code = 0
        elif args.command == "http-check":
            device = Device(
                HttpTransport(args.host),
                args.device_id,
                args.firmware_commit,
                args.model,
                args.profile,
            )
            outcomes = run_http(
                device,
                Deadline(args.timeout_seconds),
                attempt,
                exercise=args.exercise,
                reserve=args.restore_reserve_seconds,
                set_level=args.set_level,
            )
            attempt.write("http.json", outcomes)
            requested = ["readiness"]
            if args.set_level is not None:
                requested.append("mode_change")
            if args.exercise:
                requested += [
                    "baseline_reboot",
                    "modes",
                    "snapshot",
                    "reboot",
                    "persistence",
                    "volatile",
                    "restoration",
                ]
            if "operation" in outcomes:
                requested.append("operation")
            code = exit_code(outcomes, requested)
        else:
            # Only this explicit live operation imports/opens the serial backend.
            import serial
            from serial.tools import list_ports

            infos = [
                port
                for port in list_ports.comports()
                if device_id(port.serial_number) == args.device_id
            ]
            if (
                len(infos) != 1
                or Path(infos[0].device).resolve() != Path(args.port).resolve()
            ):
                raise CheckError("usb_identity_mismatch")
            stream = serial.Serial(
                port=None, baudrate=115200, timeout=0.2, exclusive=True
            )
            stream.dtr, stream.rts, stream.port = False, False, args.port
            stream.open()
            with stream:
                startup, summary = capture_uart(
                    stream, args.startup_seconds, args.duration_seconds, attempt
                )
            fields = {**startup["fields"], **summary["fields"]}
            expected = {
                "model": args.model,
                "profile": args.profile,
                "commit": args.firmware_commit,
            }
            if (
                startup["identity_conflicts"]
                or summary["identity_conflicts"]
                or any(
                    key in part["fields"] and part["fields"][key] != value
                    for part in (startup, summary)
                    for key, value in expected.items()
                )
            ):
                identity = result("FAIL", "build_mismatch")
            elif not all(key in fields for key in expected):
                identity = result("NOT_CHECKED", "build_witness_unavailable")
            else:
                identity = result("PASS", "usb_and_build_verified")
            outcomes = {
                "build_identity": identity,
                "uart": uart_check(summary, args.profile, args.model, args.mode),
                "mode_confirmation": result(
                    "NOT_CHECKED", "declared_mode_not_http_verified"
                ),
            }
            for name in ("build_identity", "uart", "mode_confirmation"):
                attempt.phase(name, outcomes[name])
            code = exit_code(outcomes, ["build_identity", "uart"])
    except KeyboardInterrupt:
        if attempt:
            try:
                attempt.phase("interrupted", result("NOT_CHECKED", "interrupted"))
            except Exception:
                pass
    except CheckError as exc:
        value = result("FAIL", str(exc))
        if attempt:
            try:
                attempt.phase("operation", value)
            except Exception:
                print(
                    json.dumps(result("FAIL", "checkpoint_write_failed")),
                    file=sys.stderr,
                )
        else:
            print(json.dumps(value), file=sys.stderr)
    except Exception:
        # Never print str/repr of input errors, HTTP exceptions or arbitrary data.
        if attempt:
            try:
                attempt.phase("operation", result("FAIL", "operation_failed"))
            except Exception:
                print(
                    json.dumps(result("FAIL", "checkpoint_write_failed")),
                    file=sys.stderr,
                )
        else:
            print(
                json.dumps(result("FAIL", "invalid_arguments_or_output")),
                file=sys.stderr,
            )
    finally:
        if attempt:
            try:
                attempt.finish(code)
            except Exception:
                print(
                    json.dumps(result("FAIL", "checkpoint_write_failed")),
                    file=sys.stderr,
                )
                code = 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
