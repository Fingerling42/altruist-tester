from pathlib import Path

from altruist_tester.identity import (
    detect_device_identity,
    format_device_id_as_mac,
    normalize_device_id,
    parse_identity_from_serial_line,
)
from altruist_tester.ports import SerialPortInfo


def test_normalize_device_id_accepts_common_mac_forms():
    assert normalize_device_id("10:51:DB:01:0C:70") == "1051DB010C70"
    assert normalize_device_id("10-51-db-01-0c-70") == "1051DB010C70"
    assert normalize_device_id("1051db010c70") == "1051DB010C70"


def test_normalize_device_id_rejects_non_device_values():
    assert normalize_device_id("Espressif") is None
    assert normalize_device_id("ABC123") is None


def test_format_device_id_as_mac():
    assert format_device_id_as_mac("1051DB010C70") == "10:51:DB:01:0C:70"


def test_parse_identity_from_firmware_chip_id_line():
    line = "[123] [INFO] ChipId: : 1051DB010C70"

    assert parse_identity_from_serial_line(line) == "1051DB010C70"


def test_parse_identity_from_data_json_sensor_id():
    line = '{"software_version":"x","sensor_id":"1051DB010C70"}'

    assert parse_identity_from_serial_line(line) == "1051DB010C70"


def test_detect_device_identity_from_usb_serial_number():
    identity = detect_device_identity(
        Path("/dev/ttyACM0"),
        port_infos=[
            SerialPortInfo(
                device="/dev/ttyACM0",
                serial_number="10:51:DB:01:0C:70",
            )
        ],
    )

    assert identity.device_id == "1051DB010C70"
    assert identity.mac == "10:51:DB:01:0C:70"
    assert identity.sources == {"usb": "1051DB010C70"}


def test_detect_device_identity_from_by_id_path():
    identity = detect_device_identity(
        Path(
            "/dev/serial/by-id/"
            "usb-Espressif_USB_JTAG_serial_debug_unit_10-51-DB-01-0C-70-if00"
        )
    )

    assert identity.device_id == "1051DB010C70"
    assert identity.by_id is not None


def test_device_identity_records_conflicts_between_sources():
    identity = detect_device_identity(
        Path("/dev/ttyACM0"),
        port_infos=[
            SerialPortInfo(
                device="/dev/ttyACM0",
                serial_number="10:51:DB:01:0C:70",
            )
        ],
    ).with_serial_log_device_id("AA:BB:CC:DD:EE:FF")

    assert identity.device_id is None
    assert identity.conflicts == (
        {"source": "serial_log", "device_id": "AABBCCDDEEFF"},
        {"source": "usb", "device_id": "1051DB010C70"},
    )
