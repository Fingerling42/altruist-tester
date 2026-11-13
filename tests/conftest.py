"""Offline defaults: tests supply discovery/transport fixtures explicitly."""

from pathlib import Path

import pytest
import serial
from serial.tools import list_ports

from altruist_tester import identity


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("offline_io_forbidden")

    # An unstubbed host inventory must not influence a CLI/rule test.
    monkeypatch.setattr(list_ports, "comports", lambda: [])
    monkeypatch.setattr(serial, "Serial", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    matching_symlink = identity._matching_symlink

    def fixture_symlink(directory, target):
        if directory in {Path("/dev/serial/by-id"), Path("/dev/serial/by-path")}:
            return None
        return matching_symlink(directory, target)

    monkeypatch.setattr(identity, "_matching_symlink", fixture_symlink)
