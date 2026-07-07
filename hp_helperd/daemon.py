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

# Keycodes treated as modifiers (linux/input-event-codes.h).
_MODIFIER_CODES = frozenset({
    29,   # KEY_LEFTCTRL
    42,   # KEY_LEFTSHIFT
    54,   # KEY_RIGHTSHIFT
    56,   # KEY_LEFTALT
    97,   # KEY_RIGHTCTRL
    100,  # KEY_RIGHTALT
    125,  # KEY_LEFTMETA
    126,  # KEY_RIGHTMETA
})

_kbd_last_input: float = 0.0
_kbd_watcher_running: bool = False
_kbd_lock = threading.Lock()
_kbd_stop = threading.Event()
_kbd_thread: threading.Thread | None = None

# Last non-modifier keypress state (for the program-shortcut feature).
_held_mods: set[int] = set()
_last_press_mods: tuple[int, ...] = ()
_last_press_key: int = 0
_last_press_seq: int = 0


def _kbd_watcher_loop() -> None:
    """Read keyboard events from the built-in keyboard + WMI hotkeys device.

    Tracks the idle time (for keyboard-backlight dimming) and, for the
    program-shortcut feature, the set of currently-held modifiers plus the
    last non-modifier keypress (with the modifiers held at that moment).

    Watches both the i8042 AT keyboard (ordinary keys + combos) and the
    "HP WMI hotkeys" device (where the OMEN key surfaces).  Retries device
    discovery/opening so a transient udev/permission issue never kills the
    watcher for the daemon's entire lifetime.
    """
    global _kbd_last_input, _kbd_watcher_running
    global _held_mods, _last_press_mods, _last_press_key, _last_press_seq
    while not _kbd_stop.is_set():
        dev_paths = _discover_keyboard_devices()
        if not dev_paths:
            with _kbd_lock:
                _kbd_watcher_running = False
            logger.warning("kbd-watch: no keyboard devices found, retrying in 5s")
            _kbd_stop.wait(5.0)
            continue
        fds = []
        opened = []
        for p in dev_paths:
            try:
                fds.append(os.open(p, os.O_RDONLY))
                opened.append(p)
            except OSError as e:
                logger.warning("kbd-watch: cannot open %s: %s", p, e)
        if not fds:
            with _kbd_lock:
                _kbd_watcher_running = False
            _kbd_stop.wait(5.0)
            continue
        with _kbd_lock:
            _kbd_watcher_running = True
        logger.info("kbd-watch: monitoring %s", ", ".join(opened))
        try:
            while not _kbd_stop.is_set():
                r, _, _ = select.select(fds, [], [], 1.0)
                if not r:
                    continue
                reopen = False
                for fd in r:
                    try:
                        data = os.read(fd, _EVENT_SIZE)
                    except OSError:
                        logger.warning("kbd-watch: read error, will reopen devices")
                        reopen = True
                        break
                    if len(data) < _EVENT_SIZE:
                        continue
                    _, _, ev_type, code, ev_value = struct.unpack(_EVENT_FORMAT, data)
                    if ev_type != _EV_KEY:
                        continue
                    with _kbd_lock:
                        if code in _MODIFIER_CODES:
                            if ev_value == 1:
                                _held_mods.add(code)
                            elif ev_value == 0:
                                _held_mods.discard(code)
                        elif ev_value == 1:  # non-modifier keypress
                            _last_press_mods = tuple(sorted(_held_mods))
                            _last_press_key = code
                            _last_press_seq += 1
                            _kbd_last_input = time.monotonic()
                if reopen:
                    break
        finally:
            with _kbd_lock:
                _kbd_watcher_running = False
                _held_mods.clear()
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if not _kbd_stop.is_set():
            _kbd_stop.wait(2.0)


def _discover_keyboard_devices() -> list[str]:
    """Collect the built-in keyboard + WMI hotkeys device paths (deduped)."""
    paths: list[str] = []
    p = sysfs.find_laptop_keyboard_device()
    if p:
        paths.append(p)
    w = sysfs.find_wmi_hotkeys_device()
    if w and w not in paths:
        paths.append(w)
    return paths


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
    """Return seconds since the last physical keypress on the laptop keyboard.

    Returns -1.0 when the watcher thread isn't actively monitoring the
    keyboard device — the GUI uses this to avoid dimming (graceful
    degradation when the device is unavailable).
    """
    with _kbd_lock:
        if not _kbd_watcher_running:
            return -1.0
        return time.monotonic() - _kbd_last_input


def kbd_last_event() -> tuple[tuple[int, ...], int, int]:
    """Return the last non-modifier keypress for the program-shortcut feature.

    Returns ``(mods, key, seq)`` where ``mods`` is the sorted tuple of
    modifier keycodes held at the moment of the press, ``key`` is the
    non-modifier keycode (0 if none recorded yet), and ``seq`` is a
    monotonic counter that bumps on every recorded press so callers can
    detect a fresh event.
    """
    with _kbd_lock:
        return _last_press_mods, _last_press_key, _last_press_seq


def _recv_line(stream: socket.socket) -> bytes | None:
    """Read exactly one newline-terminated line from `stream`.

    Returns the line (with the trailing `\\n`), or `None` on socket error
    or EOF before any data.
    """
    data = b""
    while not data.endswith(b"\n"):
        try:
            chunk = stream.recv(1024)
        except OSError:
            return None
        if not chunk:
            return None if not data else data
        data += chunk
    return data


def _parse_int(body: str, name: str) -> int:
    """Parse a single integer body, raising RuntimeError with a clear name on failure."""
    try:
        return int(body)
    except ValueError:
        raise RuntimeError(f"invalid {name}") from None


def _parse_rgb(body: str) -> tuple[int, int, int]:
    """Parse three tab-separated integers (r, g, b)."""
    parts = body.split("\t")
    if len(parts) != 3:
        raise RuntimeError("expected 3 integers")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        raise RuntimeError("invalid integer") from None


def _make_dispatch(sampler: RaplPowerSampler, sampler_lock: threading.Lock | None):
    """Build the (prefix -> handler) table for the request dispatch.

    Each handler takes the request body (request minus its prefix) and
    returns the response line as a string. RuntimeError propagates to the
    caller as a structured `ERR` response.
    """
    def _cpu_power(_body: str) -> str:
        logger.info("[cpu-power] daemon request")
        if sampler_lock is not None:
            with sampler_lock:
                sample = sampler.read()
        else:
            sample = sampler.read()
        return protocol.format_cpu_power_response(sample)

    def _fan_auto(_body: str) -> str:
        logger.info("[fan-control] daemon request: fan-auto")
        try:
            result = sysfs.write_pwm_enable(2)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _fan_manual(_body: str) -> str:
        logger.info("[fan-control] daemon request: fan-manual")
        try:
            result = sysfs.write_pwm_enable(1)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _fan_pwm(body: str) -> str:
        pwm = _parse_int(body, "pwm")
        logger.info("[fan-control] daemon request: fan-pwm %d/255 (%d%%)", pwm, round(pwm / 255.0 * 100))
        try:
            result = sysfs.write_pwm(pwm)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _keyboard_color(body: str) -> str:
        r, g, b = _parse_rgb(body)
        logger.info("[keyboard-rgb] daemon request: color %d %d %d", r, g, b)
        result = sysfs.write_keyboard_color(r, g, b)
        return protocol.format_status_response((True, result))

    def _keyboard_brightness(body: str) -> str:
        level = _parse_int(body, "brightness")
        logger.info("[keyboard-rgb] daemon request: brightness %d/255", level)
        try:
            result = sysfs.write_keyboard_brightness(level)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _keyboard_last_event(_body: str) -> str:
        logger.info("[keyboard] daemon request: keyboard-last-event")
        mods, key, seq = kbd_last_event()
        mods_csv = ",".join(str(m) for m in mods)
        return f"OK\t{mods_csv}\t{key}\t{seq}\n"

    def _power_limits(body: str) -> str:
        s, f, sl = _parse_rgb(body)  # same shape: three tab-separated ints
        logger.info("[power-limits] daemon request: STAPM=%d fast=%d slow=%d", s, f, sl)
        result = ryzenadj.apply_power_limits(s, f, sl)
        return protocol.format_status_response((True, result))

    def _keyboard_last_input(_body: str) -> str:
        logger.info("[keyboard-rgb] daemon request: keyboard-last-input")
        elapsed = kbd_elapsed_since_last_input()
        return f"OK\t{elapsed:.3f}\n"

    return [
        ("cpu-power", _cpu_power),
        ("fan-auto", _fan_auto),
        ("fan-pwm\t", _fan_pwm),
        ("fan-manual", _fan_manual),
        ("keyboard-color\t", _keyboard_color),
        ("power-limits\t", _power_limits),
        ("keyboard-brightness\t", _keyboard_brightness),
        ("keyboard-last-input", _keyboard_last_input),
        ("keyboard-last-event", _keyboard_last_event),
    ]




def handle_client(stream: socket.socket, sampler: RaplPowerSampler, sampler_lock: threading.Lock | None = None) -> None:
    """Handle one client connection. Runs in its own thread."""
    data = _recv_line(stream)
    if data is None:
        try:
            stream.sendall(b"ERR\tempty request\n")
        except OSError:
            pass
        return

    request = data.decode().rstrip("\n")
    dispatch = _make_dispatch(sampler, sampler_lock)
    matched_prefix: str | None = None
    handler = None
    for prefix, h in dispatch:
        if request.startswith(prefix):
            matched_prefix = prefix
            handler = h
            break

    try:
        if handler is None or matched_prefix is None:
            response = protocol.format_status_response((False, "unsupported request"))
        else:
            body = request[len(matched_prefix):]
            response = handler(body)
    except RuntimeError as e:
        response = protocol.format_status_response((False, str(e)))
    except Exception:
        logger.exception("request handler crashed: %r", request)
        response = None

    if response is not None:
        try:
            stream.sendall(response.encode())
        except OSError as e:
            logger.warning("failed to write response: %s", e)
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
                handle_client(s, sampler, sampler_lock)
            except Exception:
                logger.exception("client handler crashed")

        t = threading.Thread(target=_handle, args=(conn,), daemon=True)
        t.start()
