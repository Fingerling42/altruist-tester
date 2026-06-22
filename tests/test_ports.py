from types import SimpleNamespace

from altruist_tester.ports import SerialPortInfo, list_serial_ports


def test_vid_pid_formats_hex_values():
    port = SerialPortInfo(device="/dev/ttyACM0", vid=0x303A, pid=0x1001)

    assert port.vid_pid == "303A:1001"


def test_vid_pid_is_empty_when_values_are_missing():
    port = SerialPortInfo(device="/dev/ttyACM0", vid=0x303A)

    assert port.vid_pid == ""


def test_list_serial_ports_sorts_detected_ports(monkeypatch):
    monkeypatch.setattr(
        "altruist_tester.ports.list_ports.comports",
        lambda: [
            SimpleNamespace(
                device="/dev/ttyS0",
                description="onboard serial",
                hwid="n/a",
                vid=None,
                pid=None,
                manufacturer=None,
                product=None,
                serial_number=None,
                location=None,
            ),
            SimpleNamespace(
                device="/dev/ttyUSB0",
                description="USB serial",
                hwid="USB VID:PID=1111:2222",
                vid=0x1111,
                pid=0x2222,
                manufacturer="Example",
                product="Example USB Serial",
                serial_number="ABC123",
                location="1-1",
            ),
            SimpleNamespace(
                device="/dev/ttyACM0",
                description="CDC device",
                hwid="USB VID:PID=303A:1001",
                vid=0x303A,
                pid=0x1001,
                manufacturer="Espressif",
                product="USB JTAG/serial debug unit",
                serial_number="10:51:DB:01:0C:70",
                location="1-2",
            ),
        ],
    )

    ports = list_serial_ports()

    assert [port.device for port in ports] == ["/dev/ttyACM0", "/dev/ttyUSB0"]
    assert ports[0].description == "CDC device"
    assert ports[0].manufacturer == "Espressif"
    assert ports[0].serial_number == "10:51:DB:01:0C:70"
