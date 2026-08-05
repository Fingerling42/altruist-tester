import json

from typer.testing import CliRunner

from altruist_tester.cli import app
from altruist_tester.ports import SerialPortInfo
from tests.cli_helpers import (
    patch_batch_worker_processes,
    plain_output,
    write_fake_worker_summary,
)


def test_batch_dry_run_prints_planned_device_runs(monkeypatch, tmp_path):
    urban_profile = tmp_path / "urban.toml"
    insight_profile = tmp_path / "insight.toml"
    urban_profile.touch()
    insight_profile.touch()
    urban_port = tmp_path / "ttyACM0"
    urban_port.touch()
    insight_port = tmp_path / "ttyACM1"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "24h"
baud = 9600
output_dir = "batch-runs"

[[devices]]
slot = "slot-01"
model = "urban"
port = "{urban_port}"
config = "urban.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "{insight_port}"
config = "insight.toml"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [
            SerialPortInfo(
                device=str(urban_port),
                description="USB JTAG/serial debug unit",
                manufacturer="Espressif",
                serial_number="58:8C:81:40:B8:EC",
            )
        ],
    )

    result = CliRunner().invoke(
        app,
        ["batch", "--config", str(batch_config), "--dry-run"],
    )

    assert result.exit_code == 0
    output = plain_output(result)
    assert "Batch dry-run" in output
    assert "- duration: 24h (86400s)" in output
    assert "- baud: 9600" in output
    assert "- output_dir: batch-runs" in output
    assert "slot-01" in output
    assert "model: urban" in output
    assert f"port: {urban_port}" in output
    assert "port_exists: yes" in output
    assert f"config: {urban_profile}" in output
    assert "device_id=588C8140B8EC" in output
    assert "mac=58:8C:81:40:B8:EC" in output
    assert "slot-02" in output
    assert "model: insight" in output
    assert f"port: {insight_port}" in output
    assert "port_exists: no" in output
    assert f"config: {insight_profile}" in output


def test_batch_explicit_ports_dry_run_generates_device_slots(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    first_port = tmp_path / "ttyACM0"
    first_port.touch()
    second_port = tmp_path / "ttyACM1"
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])

    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--port",
            first_port,
            "--port",
            second_port,
            "--duration",
            "10s",
            "--device-config",
            str(profile),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    output = plain_output(result)
    assert "Batch dry-run" in output
    assert "- duration: 10s (10s)" in output
    assert "- default_config:" in output
    assert "device-01" in output
    assert "device-02" in output
    assert f"port: {first_port}" in output
    assert f"port: {second_port}" in output
    assert "port_exists: yes" in output
    assert "port_exists: no" in output
    assert f"config: {profile}" in output


def test_batch_explicit_ports_run_uses_shared_device_config(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    first_port = "/dev/serial/by-path/first"
    second_port = "/dev/serial/by-path/second"
    started = []
    patch_batch_worker_processes(monkeypatch, started=started)

    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--port",
            first_port,
            "--port",
            second_port,
            "--duration",
            "10s",
            "--device-config",
            str(profile),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert len(started) == 2
    assert started[0].args[started[0].args.index("--port") + 1] == first_port
    assert started[1].args[started[1].args.index("--port") + 1] == second_port
    assert started[0].args[started[0].args.index("--config") + 1] == str(profile)
    assert started[1].args[started[1].args.index("--config") + 1] == str(profile)
    assert started[0].args[started[0].args.index("--duration") + 1] == "10s"

    batch_dir = next(output_dir.iterdir())
    assert (batch_dir / "devices" / "device-01").exists()
    assert (batch_dir / "devices" / "device-02").exists()
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["devices"][0]["slot"] == "device-01"
    assert summary["devices"][1]["slot"] == "device-02"


def test_batch_explicit_ports_requires_duration(tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()

    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--port",
            "/dev/serial/by-path/first",
            "--device-config",
            str(profile),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "Specify --duration" in plain_output(result)


def test_batch_rejects_config_mixed_with_explicit_ports(tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
device_config = "{profile}"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/slot-01"
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--config",
            str(batch_config),
            "--port",
            "/dev/serial/by-path/other",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "Use either --config or explicit --port mode" in plain_output(result)


def test_batch_runs_workers_as_subprocesses(monkeypatch, tmp_path):
    urban_profile = tmp_path / "urban.toml"
    insight_profile = tmp_path / "insight.toml"
    urban_profile.touch()
    insight_profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "24h"
baud = 9600
output_dir = "{output_dir}"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/urban"
config = "urban.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/insight"
config = "insight.toml"
expected_sensors = ["scd41"]
expected_metrics = ["co2"]
""",
        encoding="utf-8",
    )
    started = []
    patch_batch_worker_processes(monkeypatch, started=started)

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 0
    assert len(started) == 2
    assert "Started batch" in result.output
    assert "Batch completed: 2/2 workers succeeded." in result.output

    urban_command = started[0].args
    insight_command = started[1].args
    assert urban_command[1:4] == ["-m", "altruist_tester.cli", "run"]
    assert urban_command[urban_command.index("--port") + 1] == (
        "/dev/serial/by-path/urban"
    )
    assert urban_command[urban_command.index("--duration") + 1] == "24h"
    assert urban_command[urban_command.index("--baud") + 1] == "9600"
    assert urban_command[urban_command.index("--config") + 1] == str(urban_profile)
    assert urban_command[urban_command.index("--device-model") + 1] == "urban"
    assert insight_command[insight_command.index("--config") + 1] == str(
        insight_profile
    )
    assert insight_command[insight_command.index("--device-model") + 1] == "insight"
    assert "--expect-sensor" in insight_command
    assert insight_command[insight_command.index("--expect-sensor") + 1] == "scd41"
    assert "--expect-metric" in insight_command
    assert insight_command[insight_command.index("--expect-metric") + 1] == "co2"

    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["workers"][0]["returncode"] == 0
    assert summary["workers"][0]["status"] == "completed"
    assert summary["workers"][1]["returncode"] == 0
    assert (batch_dir / "devices" / "slot-01" / "worker.stdout.log").read_text() == (
        "worker stdout\n"
    )
    assert (batch_dir / "devices" / "slot-02" / "worker.stderr.log").read_text() == (
        "worker stderr\n"
    )


def test_batch_prints_live_progress_while_workers_run(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/slot-01"

[[devices]]
slot = "slot-02"
model = "urban"
port = "/dev/serial/by-path/slot-02"
""",
        encoding="utf-8",
    )

    class FakeProcess:
        def __init__(self, args, stdout, stderr, text):
            self.args = list(args)
            self.poll_count = 0
            self.summary_written = False
            stdout.write("worker stdout\n")
            stderr.write("worker stderr\n")

        def poll(self):
            self.poll_count += 1
            if self.poll_count == 1:
                return None
            if not self.summary_written:
                write_fake_worker_summary(self.args)
                self.summary_written = True
            return 0

    monkeypatch.setattr("altruist_tester.cli.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("altruist_tester.cli.time.sleep", lambda seconds: None)

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 0
    output = plain_output(result)
    assert "Batch   0.0% (00:00/1:00:00)" in output
    assert "running=2 completed=0 failed=0" in output
    assert "Slots: slot-01=running slot-02=running" in output
    assert "running=0 completed=2 failed=0" in output
    assert "Slots: slot-01=completed slot-02=completed" in output


def test_batch_interrupt_terminates_workers_and_writes_summary(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/completes"

[[devices]]
slot = "slot-02"
model = "urban"
port = "/dev/serial/by-path/running"
""",
        encoding="utf-8",
    )
    processes = []

    class FakeProcess:
        def __init__(self, args, stdout, stderr, text):
            self.args = list(args)
            self.port = args[args.index("--port") + 1]
            self.returncode = None
            self.summary_written = False
            self.terminated = False
            stdout.write("worker stdout\n")
            stderr.write("worker stderr\n")
            processes.append(self)

        def poll(self):
            if self.port == "/dev/serial/by-path/completes":
                if not self.summary_written:
                    write_fake_worker_summary(self.args)
                    self.summary_written = True
                self.returncode = 0
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    sleep_calls = {"count": 0}

    def fake_sleep(seconds):
        if sleep_calls["count"] == 0:
            sleep_calls["count"] += 1
            raise KeyboardInterrupt
        sleep_calls["count"] += 1

    monkeypatch.setattr("altruist_tester.cli.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("altruist_tester.cli.time.sleep", fake_sleep)

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 130
    assert "Batch interrupted: 1/2 workers completed before shutdown." in result.output
    assert processes[1].terminated is True
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "interrupted"
    assert summary["message"] == (
        "Batch interrupted: 1/2 workers completed before shutdown."
    )
    assert summary["workers"][0]["slot"] == "slot-01"
    assert summary["workers"][0]["status"] == "completed"
    assert summary["workers"][1]["slot"] == "slot-02"
    assert summary["workers"][1]["status"] == "interrupted"
    assert summary["workers"][1]["failure_kind"] == "interrupted"
    assert summary["device_results"][0]["slot"] == "slot-01"
    assert summary["device_results"][0]["verdict"] == "PASS_CANDIDATE"
    assert summary["device_results"][1]["slot"] == "slot-02"
    assert summary["device_results"][1]["summary_error"] == "summary.json not found"
    assert (batch_dir / "batch_report.txt").exists()


def test_batch_returns_failure_when_any_worker_fails(monkeypatch, tmp_path):
    urban_profile = tmp_path / "urban.toml"
    insight_profile = tmp_path / "insight.toml"
    urban_profile.touch()
    insight_profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/urban"
config = "urban.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/insight"
config = "insight.toml"
""",
        encoding="utf-8",
    )
    returncodes = {
        "/dev/serial/by-path/urban": 0,
        "/dev/serial/by-path/insight": 1,
    }
    patch_batch_worker_processes(
        monkeypatch,
        returncodes_by_port=returncodes,
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 1
    assert "Batch completed: 1/2 workers succeeded." in result.output
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["workers"][0]["returncode"] == 0
    assert summary["workers"][1]["returncode"] == 1
    assert summary["workers"][1]["status"] == "failed"
    assert summary["workers"][1]["failure_kind"] == "health_check_failed"


def test_batch_collects_per_device_summary_results(monkeypatch, tmp_path):
    urban_profile = tmp_path / "urban.toml"
    insight_profile = tmp_path / "insight.toml"
    urban_profile.touch()
    insight_profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/urban"
config = "urban.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/insight"
config = "insight.toml"
""",
        encoding="utf-8",
    )

    patch_batch_worker_processes(
        monkeypatch,
        summaries_by_port={
            "/dev/serial/by-path/urban": {
                "status": "completed",
                "verdict": "PASS_CANDIDATE",
                "device_identity": {
                    "device_id": "588C8140B8EC",
                    "mac": "58:8C:81:40:B8:EC",
                    "usb_serial": "58:8C:81:40:B8:EC",
                    "by_id": "/dev/serial/by-id/usb-Espressif_588C8140B8EC",
                    "by_path": "/dev/serial/by-path/urban",
                },
                "findings": [],
                "rules": {"failed_checks": []},
                "upload_health": {"status": "ok"},
                "sensor_presence": {"status": "ok"},
            },
            "/dev/serial/by-path/insight": {
                "status": "failed",
                "verdict": "FAIL",
                "device_identity": {
                    "device_id": None,
                    "mac": "10:51:DB:01:0C:70",
                    "usb_serial": "10:51:DB:01:0C:70",
                    "by_id": "/dev/serial/by-id/usb-Espressif_1051DB010C70",
                    "by_path": "/dev/serial/by-path/insight",
                    "conflicts": [
                        {"source": "serial_log", "device_id": "AABBCCDDEEFF"},
                        {"source": "usb", "device_id": "1051DB010C70"},
                    ],
                },
                "findings": [
                    {
                        "severity": "fail",
                        "code": "MISSING_SENSOR_METRIC",
                        "message": "Missing expected sensor metrics: co2",
                    }
                ],
                "rules": {"failed_checks": ["sensor_presence"]},
                "upload_health": {"status": "warn"},
                "sensor_presence": {"status": "fail"},
            },
        },
        reports_by_port={
            "/dev/serial/by-path/urban": "device report\n",
            "/dev/serial/by-path/insight": "device report\n",
        },
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 1
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["verdict"] == "FAIL"
    assert summary["devices_total"] == 2
    assert summary["devices_passed"] == 1
    assert summary["devices_warned"] == 0
    assert summary["devices_failed"] == 1
    assert summary["devices"][0]["slot"] == "slot-01"
    assert summary["devices"][0]["device_id"] == "588C8140B8EC"
    assert summary["devices"][0]["usb_serial"] == "58:8C:81:40:B8:EC"
    assert summary["devices"][0]["by_id"].endswith("usb-Espressif_588C8140B8EC")
    assert summary["devices"][0]["by_path"] == "/dev/serial/by-path/urban"
    assert summary["devices"][0]["identity_conflicts"] == []
    assert summary["devices"][0]["verdict"] == "PASS_CANDIDATE"
    assert summary["devices"][1]["slot"] == "slot-02"
    assert summary["devices"][1]["device_id"] is None
    assert summary["devices"][1]["mac"] == "10:51:DB:01:0C:70"
    assert summary["devices"][1]["usb_serial"] == "10:51:DB:01:0C:70"
    assert summary["devices"][1]["by_id"].endswith("usb-Espressif_1051DB010C70")
    assert summary["devices"][1]["by_path"] == "/dev/serial/by-path/insight"
    assert summary["devices"][1]["identity_conflicts"] == [
        {"source": "serial_log", "device_id": "AABBCCDDEEFF"},
        {"source": "usb", "device_id": "1051DB010C70"},
    ]
    assert summary["devices"][1]["verdict"] == "FAIL"
    assert summary["devices"][1]["report_txt"].endswith("/report.txt")
    assert summary["devices"][1]["finding_messages"] == [
        "MISSING_SENSOR_METRIC: Missing expected sensor metrics: co2"
    ]
    assert len(summary["device_results"]) == 2
    assert summary["device_results"][0]["slot"] == "slot-01"
    assert summary["device_results"][0]["model"] == "urban"
    assert summary["device_results"][0]["verdict"] == "PASS_CANDIDATE"
    assert summary["device_results"][0]["device_identity"]["device_id"] == (
        "588C8140B8EC"
    )
    assert summary["device_results"][0]["findings_count"] == 0
    assert summary["device_results"][1]["slot"] == "slot-02"
    assert summary["device_results"][1]["model"] == "insight"
    assert summary["device_results"][1]["verdict"] == "FAIL"
    assert summary["device_results"][1]["failed_checks"] == ["sensor_presence"]
    assert summary["device_results"][1]["report_txt"].endswith("/report.txt")
    assert summary["device_results"][1]["upload_health"]["status"] == "warn"
    assert summary["device_results"][1]["sensor_presence"]["status"] == "fail"
    report = (batch_dir / "batch_report.txt").read_text()
    assert "Verdict: FAIL" in report
    assert "Devices: 2 total, 1 pass, 0 warn, 1 fail" in report
    assert "- slot-01 588C8140B8EC PASS_CANDIDATE (urban, 0 findings)" in report
    assert "- slot-02 10:51:DB:01:0C:70 FAIL (insight, 1 findings)" in report
    assert "usb serial: 58:8C:81:40:B8:EC" in report
    assert "by-id: /dev/serial/by-id/usb-Espressif_588C8140B8EC" in report
    assert "by-path: /dev/serial/by-path/insight" in report
    assert "identity warning: conflicting identity sources" in report
    assert "serial_log: AABBCCDDEEFF" in report
    assert "failed checks: sensor_presence" in report
    assert "report:" in report
    assert "MISSING_SENSOR_METRIC: Missing expected sensor metrics: co2" in report


def test_batch_summary_verdict_warns_without_failures(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/pass"

[[devices]]
slot = "slot-02"
model = "urban"
port = "/dev/serial/by-path/warn"
""",
        encoding="utf-8",
    )
    patch_batch_worker_processes(
        monkeypatch,
        summaries_by_port={
            "/dev/serial/by-path/warn": {
                "status": "completed",
                "verdict": "WARN",
                "device_identity": {},
                "findings": [{"severity": "warn"}],
                "rules": {"failed_checks": []},
            },
        },
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 0
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["verdict"] == "WARN"
    assert summary["devices_total"] == 2
    assert summary["devices_passed"] == 1
    assert summary["devices_warned"] == 1
    assert summary["devices_failed"] == 0


def test_batch_report_lists_all_device_findings(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/failing"
""",
        encoding="utf-8",
    )

    findings = [
        {
            "severity": "fail",
            "code": f"CHECK_{index}",
            "message": f"Finding {index}",
        }
        for index in range(1, 6)
    ]
    patch_batch_worker_processes(
        monkeypatch,
        summaries_by_port={
            "/dev/serial/by-path/failing": {
                "status": "failed",
                "verdict": "FAIL",
                "device_identity": {},
                "findings": findings,
                "rules": {"failed_checks": ["sensor_presence"]},
            },
        },
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 1
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["devices"][0]["findings_count"] == 5
    assert summary["devices"][0]["finding_messages"] == [
        f"CHECK_{index}: Finding {index}" for index in range(1, 6)
    ]
    report = (batch_dir / "batch_report.txt").read_text()
    for index in range(1, 6):
        assert f"CHECK_{index}: Finding {index}" in report


def test_batch_warns_when_device_identity_is_missing(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/urban"
""",
        encoding="utf-8",
    )
    patch_batch_worker_processes(
        monkeypatch,
        summaries_by_port={
            "/dev/serial/by-path/urban": {
                "status": "completed",
                "verdict": "PASS_CANDIDATE",
                "findings": [],
                "rules": {"failed_checks": []},
            },
        },
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 0
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "completed"
    assert summary["verdict"] == "PASS_CANDIDATE"
    assert summary["devices_failed"] == 0
    assert summary["devices"][0]["slot"] == "slot-01"
    assert summary["devices"][0]["device_id"] is None
    assert summary["devices"][0]["mac"] is None
    assert summary["devices"][0]["usb_serial"] is None
    assert summary["devices"][0]["by_id"] is None
    assert summary["devices"][0]["by_path"] is None
    assert summary["devices"][0]["identity_conflicts"] == []
    report = (batch_dir / "batch_report.txt").read_text()
    assert "- slot-01 unknown-device PASS_CANDIDATE (urban, 0 findings)" in report
    assert "identity warning: identity was not resolved" in report


def test_batch_marks_worker_exit_2_as_infrastructure_failure(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/missing"

[[devices]]
slot = "slot-02"
model = "urban"
port = "/dev/serial/by-path/working"
""",
        encoding="utf-8",
    )
    returncodes = {
        "/dev/serial/by-path/missing": 2,
        "/dev/serial/by-path/working": 0,
    }
    patch_batch_worker_processes(
        monkeypatch,
        returncodes_by_port=returncodes,
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 1
    assert "slot-01 finished with exit code 2." in result.output
    assert "slot-02 finished with exit code 0." in result.output
    assert "Batch completed: 1/2 workers succeeded." in result.output
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["workers"][0]["slot"] == "slot-01"
    assert summary["workers"][0]["returncode"] == 2
    assert summary["workers"][0]["failure_kind"] == "infrastructure_or_config_failed"
    assert summary["workers"][1]["slot"] == "slot-02"
    assert summary["workers"][1]["returncode"] == 0
    assert summary["verdict"] == "FAIL"
    assert summary["devices_failed"] == 1


def test_batch_records_worker_start_failure_and_continues(monkeypatch, tmp_path):
    profile = tmp_path / "urban.toml"
    profile.touch()
    output_dir = tmp_path / "batch-runs"
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        f"""
[batch]
duration = "1h"
output_dir = "{output_dir}"
device_config = "urban.toml"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/start-fails"

[[devices]]
slot = "slot-02"
model = "urban"
port = "/dev/serial/by-path/working"
""",
        encoding="utf-8",
    )
    patch_batch_worker_processes(
        monkeypatch,
        start_failures_by_port={
            "/dev/serial/by-path/start-fails": "cannot exec worker",
        },
    )

    result = CliRunner().invoke(app, ["batch", "--config", str(batch_config)])

    assert result.exit_code == 1
    assert "slot-01 could not start: cannot exec worker" in result.output
    assert "slot-02 finished with exit code 0." in result.output
    batch_dir = next(output_dir.iterdir())
    summary = json.loads((batch_dir / "batch_summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["workers"][0]["slot"] == "slot-01"
    assert summary["workers"][0]["returncode"] is None
    assert summary["workers"][0]["failure_kind"] == "worker_start_failed"
    assert summary["workers"][0]["error"] == "cannot exec worker"
    assert summary["workers"][1]["slot"] == "slot-02"
    assert summary["workers"][1]["returncode"] == 0
    assert summary["verdict"] == "FAIL"
    assert summary["devices_failed"] == 1
    stderr_log = batch_dir / "devices" / "slot-01" / "worker.stderr.log"
    assert stderr_log.read_text() == "Could not start worker: cannot exec worker\n"


def test_batch_dry_run_rejects_invalid_config(tmp_path):
    batch_config = tmp_path / "batch.toml"
    batch_config.write_text(
        """
[batch]
duration = "1h"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/slot-01"
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["batch", "--config", str(batch_config), "--dry-run"],
    )

    assert result.exit_code == 2
    assert "devices[0].config is required" in plain_output(result)
