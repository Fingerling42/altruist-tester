from typer.testing import CliRunner

from altruist_tester.cli import app
from altruist_tester.ports import SerialPortInfo


def test_ports_lists_detected_serial_ports(monkeypatch):
    monkeypatch.setattr(
        "altruist_tester.cli.list_serial_ports",
        lambda: [
            SerialPortInfo(
                device="/dev/ttyACM0",
                description="USB JTAG/serial debug unit",
                hwid="USB VID:PID=303A:1001",
                vid=0x303A,
                pid=0x1001,
                manufacturer="Espressif",
                serial_number="10:51:DB:01:0C:70",
            )
        ],
    )

    result = CliRunner().invoke(app, ["ports"])

    assert result.exit_code == 0
    assert "/dev/ttyACM0" in result.output
    assert "USB JTAG/serial debug unit" in result.output
    assert "Espressif" in result.output
    assert "VID:PID=303A:1001" in result.output
    assert "SER=10:51:DB:01:0C:70" in result.output
    assert "device_id=1051DB010C70" in result.output


def test_ports_handles_empty_list(monkeypatch):
    monkeypatch.setattr("altruist_tester.cli.list_serial_ports", lambda: [])

    result = CliRunner().invoke(app, ["ports"])

    assert result.exit_code == 0
    assert "No serial ports found." in result.output
