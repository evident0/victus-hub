"""Fail closed if a pytest case tries to control the running machine."""

import socket
from pathlib import Path

import pytest


_LIVE_DAEMON_SOCKET = "/run/victus-hubd/victus-hub.sock"


@pytest.fixture(autouse=True)
def block_live_hardware(monkeypatch):
    """Allow fake sockets and temporary files, but never the real controls."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    write_text = Path.write_text
    attempts = []

    def check_socket(address):
        if address == _LIVE_DAEMON_SOCKET or address == _LIVE_DAEMON_SOCKET.encode():
            attempts.append(f"daemon socket: {address!r}")
            raise AssertionError("test attempted to connect to the live Victus Hub daemon")

    def guarded_connect(sock, address):
        check_socket(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        check_socket(address)
        return connect_ex(sock, address)

    def guarded_write_text(path, data, *args, **kwargs):
        if path.is_relative_to("/sys"):
            attempts.append(f"sysfs write: {path}")
            raise AssertionError("test attempted to write to live sysfs")
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    yield
    assert not attempts, "live hardware access was attempted: " + ", ".join(attempts)
