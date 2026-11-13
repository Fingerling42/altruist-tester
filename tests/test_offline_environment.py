import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import serial
from serial.tools import list_ports

from altruist_tester import identity


def test_default_inventory_is_empty_without_host_discovery():
    assert list_ports.comports() == []
    assert identity._matching_symlink(Path("/dev/serial/by-id"), Path("unused")) is None


def test_real_serial_is_not_a_test_fixture():
    with pytest.raises(AssertionError, match="offline_io_forbidden"):
        serial.Serial("/dev/not-a-test-device")


def test_network_is_not_a_test_fixture():
    with pytest.raises(AssertionError, match="offline_io_forbidden"):
        socket.create_connection(("192.0.2.10", 80))


def test_explicit_discovery_fixture_is_still_used(monkeypatch):
    fixture = SimpleNamespace(device="fixture", serial_number="001122334455")
    monkeypatch.setattr(list_ports, "comports", lambda: [fixture])
    assert list_ports.comports() == [fixture]


def test_symlink_algorithm_remains_testable_on_synthetic_files(tmp_path):
    target = tmp_path / "fixture-port"
    target.touch()
    directory = tmp_path / "by-id"
    directory.mkdir()
    link = directory / "synthetic-device"
    link.symlink_to(target)
    assert identity._matching_symlink(directory, target) == str(link)
