"""Authenticate Unix peers against logind, failing closed if it is unavailable."""

import ctypes
import socket
import struct
import subprocess
from dataclasses import dataclass


def _session_for_peer(pid: int, uid: int) -> str:
    systemd = ctypes.CDLL("libsystemd.so.0")
    libc = ctypes.CDLL(None)
    libc.free.argtypes = [ctypes.c_void_p]
    value = ctypes.c_char_p()
    # Desktop apps launched by the user manager may not belong to a session
    # scope. In that case logind's display session is the authorization scope.
    for name, argument in (("sd_pid_get_session", pid), ("sd_uid_get_display", uid)):
        fn = getattr(systemd, name)
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_char_p)]
        fn.restype = ctypes.c_int
        if fn(argument, ctypes.byref(value)) >= 0:
            try:
                return value.value.decode() if value.value else ""
            finally:
                libc.free(value)
    return ""


@dataclass(frozen=True)
class Peer:
    uid: int
    session: str = ""

    def authorized(self) -> bool:
        if self.uid == 0:
            return True
        if not self.session:
            return False
        try:
            result = subprocess.run(
                ["/usr/bin/loginctl", "show-session", self.session, "--no-pager",
                 "-p", "User", "-p", "Active", "-p", "Remote", "-p", "LockedHint",
                 "-p", "Type", "-p", "Seat"],
                capture_output=True, text=True, timeout=1,
            )
            props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            return (
                result.returncode == 0 and props.get("User") == str(self.uid)
                and props.get("Active") == "yes" and props.get("Remote") == "no"
                and props.get("LockedHint") == "no"
                and props.get("Type") in {"x11", "wayland"}
                and props.get("Seat") == "seat0"
            )
        except (OSError, subprocess.TimeoutExpired):
            return False


def authenticate(stream: socket.socket) -> Peer:
    pid, uid, _gid = struct.unpack(
        "3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    try:
        session = _session_for_peer(pid, uid) if uid else ""
    except (OSError, AttributeError):
        session = ""
    peer = Peer(uid, session)
    if not peer.authorized():
        raise RuntimeError("access denied: an active, unlocked local desktop session is required")
    return peer
