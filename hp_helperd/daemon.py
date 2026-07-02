"""Root daemon that listens on a Unix socket and executes privileged operations.

Ports privileged/daemon.rs exactly.
"""

import logging
import os
import select
import struct
import socket
import threading
import time

from hp_helper.backend import protocol
from hp_helper.backend.rapl import RaplPowerSampler
from hp_helperd import ryzenadj, sysfs

SOCKET_PATH = "/run/hp-helperd/hp-helper-rs.sock"


logger = logging.getLogger(__name__)
# ── Keyboard input watcher ──

_EVENT_FORMAT = "llHHI"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)
_EV_KEY = 1

_kbd_last_input: float = 0.0
_kbd_lock = threading.Lock()
_kbd_stop = threading.Event()
_kbd_thread: threading.Thread | None = None


def _kbd_watcher_loop() -> None:
    """Read laptop keyboard events and record last keypress time."""
    global _kbd_last_input
    dev_path = sysfs.find_laptop_keyboard_device()
    if dev_path is None:
        logger.warning("kbd-watch: no laptop keyboard device found")
        return
    try:
        fd = os.open(dev_path, os.O_RDONLY)
    except OSError as e:
        logger.warning("kbd-watch: cannot open %s: %s", dev_path, e)
        return
    logger.info("kbd-watch: monitoring %s", dev_path)
    try:
        while not _kbd_stop.is_set():
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue
            try:
                data = os.read(fd, _EVENT_SIZE)
            except OSError:
                break
            if len(data) < _EVENT_SIZE:
                continue
            _, _, ev_type, _code, ev_value = struct.unpack(_EVENT_FORMAT, data)
            if ev_type == _EV_KEY and ev_value == 1:
                with _kbd_lock:
                    _kbd_last_input = time.monotonic()
    finally:
        os.close(fd)


def start_keyboard_watcher() -> None:
    """Start the keyboard watcher thread (idempotent)."""
    global _kbd_thread, _kbd_last_input
    with _kbd_lock:
        _kbd_last_input = time.monotonic()
    if _kbd_thread is not None and _kbd_thread.is_alive():
        return
    _kbd_stop.clear()
    _kbd_thread = threading.Thread(target=_kbd_watcher_loop, daemon=True, name="kbd-watch")
    _kbd_thread.start()


def kbd_elapsed_since_last_input() -> float:
    """Return seconds since the last physical keypress on the laptop keyboard."""
    with _kbd_lock:
        return time.monotonic() - _kbd_last_input


def _parse_u8(value: str | None, name: str) -> int:
    if value is None:
        raise RuntimeError(f"missing {name}")
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"invalid {name}")


def _parse_power_limit(value: str | None, name: str) -> int:
    if value is None:
        raise RuntimeError(f"missing {name}")
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"invalid {name}")


def _parse_power_limit_request(request: str) -> tuple[int, int, int]:
    body = request.removeprefix("power-limits\t")
    parts = body.split("\t")
    if len(parts) > 3:
        raise RuntimeError("too many power limit fields")
    stapm = _parse_power_limit(parts[0] if len(parts) > 0 else None, "stapm-limit")
    fast = _parse_power_limit(parts[1] if len(parts) > 1 else None, "fast-limit")
    slow = _parse_power_limit(parts[2] if len(parts) > 2 else None, "slow-limit")
    return stapm, fast, slow


def _parse_keyboard_color_request(request: str) -> tuple[int, int, int]:
    body = request.removeprefix("keyboard-color\t")
    parts = body.split("\t")
    if len(parts) > 3:
        raise RuntimeError("too many keyboard color fields")
    red = _parse_u8(parts[0] if len(parts) > 0 else None, "red")
    green = _parse_u8(parts[1] if len(parts) > 1 else None, "green")
    blue = _parse_u8(parts[2] if len(parts) > 2 else None, "blue")
    return red, green, blue


def handle_client(stream: socket.socket, sampler: RaplPowerSampler) -> None:
    """Handle one client connection. Runs in its own thread."""
    try:
        data = b""
        while not data.endswith(b"\n"):
            chunk = stream.recv(1024)
            if not chunk:
                break
            data += chunk
    except OSError as e:
        try:
            stream.sendall(f"ERR\t{e}\n".encode())
        except OSError:
            pass
        return

    if not data:
        try:
            stream.sendall(b"ERR\tempty request\n")
        except OSError:
            pass
        return

    request = data.decode().rstrip("\n")
    try:
        if request == "cpu-power":
            logger.info("[cpu-power] daemon request")
            sample = sampler.read()
            response = protocol.format_cpu_power_response(sample)
        elif request == "fan-auto":
            logger.info("[fan-control] daemon request: fan-auto")
            try:
                result = sysfs.write_pwm_enable(2)
                response = protocol.format_status_response((True, result))
            except RuntimeError as e:
                response = protocol.format_status_response((False, str(e)))
        elif request.startswith("fan-pwm\t"):
            body = request.removeprefix("fan-pwm\t")
            try:
                pwm = _parse_u8(body, "pwm")
            except RuntimeError:
                response = "ERR\tinvalid PWM value\n"
            else:
                logger.info("[fan-control] daemon request: fan-pwm %d/255 (%d%%)", pwm, round(pwm / 255.0 * 100))
                try:
                    result = sysfs.write_pwm(pwm)
                    response = protocol.format_status_response((True, result))
                except RuntimeError as e:
                    response = protocol.format_status_response((False, str(e)))
        elif request.startswith("keyboard-color\t"):
            try:
                r, g, b = _parse_keyboard_color_request(request)
                logger.info("[keyboard-rgb] daemon request: color %d %d %d", r, g, b)
                result = sysfs.write_keyboard_color(r, g, b)
                response = protocol.format_status_response((True, result))
            except RuntimeError as e:
                response = protocol.format_status_response((False, str(e)))
        elif request.startswith("power-limits\t"):
            try:
                s, f, sl = _parse_power_limit_request(request)
                logger.info("[power-limits] daemon request: STAPM=%d fast=%d slow=%d", s, f, sl)
                result = ryzenadj.apply_power_limits(s, f, sl)
                response = protocol.format_status_response((True, result))
            except RuntimeError as e:
                response = protocol.format_status_response((False, str(e)))
        elif request == "keyboard-last-input":
            elapsed = kbd_elapsed_since_last_input()
            response = f"OK\t{elapsed:.3f}\n"
        else:
            response = "ERR\tunsupported request\n"
    except Exception as e:
        response = f"ERR\t{e}\n"

    try:
        stream.sendall(response.encode())
    except OSError as e:
        logger.warning("failed to write response: %s", e)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_daemon() -> None:
    """Start the hp-helperd Unix socket daemon."""
    socket_path = SOCKET_PATH

    # Clean up stale socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    os.makedirs("/run/hp-helperd", exist_ok=True, mode=0o755)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chmod(socket_path, 0o666)
    server.listen(5)

    sampler = RaplPowerSampler()
    # Use a lock because sampler.read() mutates self._sample
    sampler_lock = threading.Lock()

    start_keyboard_watcher()

    print(f"hp-helperd listening on {socket_path}")

    while True:
        try:
            conn, _addr = server.accept()
        except OSError:
            break

        def _handle(s: socket.socket):
            try:
                with sampler_lock:
                    handle_client(s, sampler)
            except Exception:
                pass

        t = threading.Thread(target=_handle, args=(conn,), daemon=True)
        t.start()
