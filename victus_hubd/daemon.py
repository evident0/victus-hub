"""Root daemon that listens on a Unix socket and executes privileged operations.

Ports privileged/daemon.rs exactly.
"""

import json
import logging
import os
import select
import signal
import struct
import socket
import threading
import time

from victus_hub.backend import protocol
from victus_hub.backend.shortcut_policy import validate_shortcut
from victus_hub.backend.fan_config import config_from_dict
from victus_hub.backend.rapl import RaplPowerSampler
from victus_hub.features.keyboard.lighting import lighting_from_dict, lighting_to_dict
from victus_hubd import cpufreq, desktop_activation, intel, ryzenadj, sysfs
from victus_hubd.auth import Peer, active_desktop_peer, authenticate
from victus_hubd.runtime import Runtime
from victus_hubd.state import power_from_dict, state_to_dict

SOCKET_PATH = "/run/victus-hubd/victus-hub.sock"


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
    464,  # KEY_FN (where exposed by firmware)
})

_kbd_last_input: float = 0.0
_kbd_watcher_running: bool = False
_kbd_lock = threading.Lock()
_kbd_stop = threading.Event()
_kbd_thread: threading.Thread | None = None

# Held modifiers are used locally; ordinary keypresses are never retained.
_held_mods: set[int] = set()
_kbd_subscribers: dict[socket.socket, Peer] = {}
_kbd_subscribers_lock = threading.Lock()
_runtime: Runtime | None = None
_activation_lock = threading.Lock()
MAX_REQUEST_BYTES = 64 * 1024
MAX_CLIENTS = 32
_client_slots = threading.BoundedSemaphore(MAX_CLIENTS)


def _publish_line(payload: bytes) -> None:
    """Authorize delivery, then send without waiting on a slow client."""
    with _kbd_subscribers_lock:
        subscribers = tuple(_kbd_subscribers.items())
    for stream, peer in subscribers:
        # Recheck on delivery: an existing connection must not bypass a lock
        # screen or fast user switch. No key events are queued for later.
        if not peer.authorized():
            continue
        with _kbd_subscribers_lock:
            if stream not in _kbd_subscribers:
                continue
            try:
                sent = stream.send(payload, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
                if sent == len(payload):
                    continue
            except OSError:
                pass
            _kbd_subscribers.pop(stream, None)
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _publish_lighting(settings) -> None:
    """Push the lighting policy that a hardware shortcut just applied."""
    body = json.dumps(lighting_to_dict(settings), separators=(",", ":"))
    _publish_line(f"LIGHTING\t{body}\n".encode())


def _start_activation(press: tuple[tuple[int, ...], int]) -> None:
    """Start one activation on demand; coalesce presses until it finishes."""
    if not _activation_lock.acquire(blocking=False):
        return
    try:
        threading.Thread(target=_activate_once, args=(press,), daemon=True,
                         name="ui-activation").start()
    except RuntimeError:
        _activation_lock.release()
        logger.exception("could not start program shortcut activation")


def _activate_once(press: tuple[tuple[int, ...], int]) -> None:
    """Perform this keypress's session lookup and D-Bus call off evdev."""
    try:
        peer = active_desktop_peer()
        runtime = _runtime
        if peer is not None and runtime is not None:
            if runtime.program_shortcuts.get(peer.uid) == press and press[1] != 0:
                desktop_activation.activate(peer)
    except Exception:
        logger.exception("program shortcut activation failed")
    finally:
        _activation_lock.release()


def _stream_shortcut_events(stream: socket.socket, peer: Peer) -> None:
    """Stream lighting changes; launch shortcuts are daemon-owned."""
    try:
        with _kbd_subscribers_lock:
            stream.sendall(b"OK\tshortcut-events\n")
            stream.setblocking(False)
            _kbd_subscribers[stream] = peer
        # The client sends no more requests. This blocks until it disconnects.
        while True:
            readable, _, _ = select.select([stream], [], [], 1)
            if readable:
                stream.recv(1)
                break
            # Stay connected during session locks, but delivery remains gated.
    except OSError:
        pass
    finally:
        with _kbd_subscribers_lock:
            _kbd_subscribers.pop(stream, None)
        stream.close()


def _record_key_event(code: int, value: int) -> None:
    """Track physical presses; releases update modifiers, repeats do not act."""
    global _kbd_last_input
    press = None
    with _kbd_lock:
        if value == 1:
            _kbd_last_input = time.monotonic()
            if code in _MODIFIER_CODES:
                _held_mods.add(code)
            else:
                press = (tuple(sorted(_held_mods)), code)
        elif value == 0 and code in _MODIFIER_CODES:
            _held_mods.discard(code)
    if press is not None:
        runtime = _runtime
        if runtime is not None:
            try:
                runtime.handle_key(press[0], press[1])
            except Exception:
                logger.exception("hardware shortcut failed")
            if runtime.program_shortcuts.matches_any(*press):
                _start_activation(press)


def _kbd_watcher_loop() -> None:
    """Read keyboard events from the built-in keyboard + WMI hotkeys device.

    Tracks the idle time (for keyboard-backlight dimming) and, for the
    program-shortcut feature, the currently-held modifiers at each physical
    non-modifier press. Activation runs only on matching presses.

    Watches both the i8042 AT keyboard (ordinary keys + combos) and the
    "HP WMI hotkeys" device (where the OMEN key surfaces).  Retries device
    discovery/opening so a transient udev/permission issue never kills the
    watcher for the daemon's entire lifetime.
    """
    global _kbd_last_input, _kbd_watcher_running
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
                r, _, _ = select.select(fds, [], [])
                reopen = False
                for fd in r:
                    try:
                        data = os.read(fd, _EVENT_SIZE)
                    except OSError:
                        logger.warning("kbd-watch: read error, will reopen devices")
                        reopen = True
                        break
                    if len(data) < _EVENT_SIZE:
                        reopen = True
                        break
                    _, _, ev_type, code, ev_value = struct.unpack(_EVENT_FORMAT, data)
                    if ev_type != _EV_KEY:
                        continue
                    _record_key_event(code, ev_value)
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


def _recv_line(stream: socket.socket) -> bytes | None:
    """Read exactly one newline-terminated line from `stream`.

    Returns the line (with the trailing `\\n`), or `None` on socket error
    or EOF before any data.
    """
    data = b""
    deadline = time.monotonic() + 5
    while not data.endswith(b"\n"):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("request timed out")
            stream.settimeout(remaining)
            chunk = stream.recv(1024)
        except OSError:
            return None
        if not chunk:
            return None if not data else data
        data += chunk
        if len(data) > MAX_REQUEST_BYTES:
            raise RuntimeError("request too large")
        if b"\n" in data and (not data.endswith(b"\n") or data.count(b"\n") != 1):
            raise RuntimeError("one request per connection is required")
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


def _parse_zone_rgb(body: str) -> tuple[int, int, int, int]:
    """Parse four tab-separated integers (zone, r, g, b)."""
    parts = body.split("\t")
    if len(parts) != 4:
        raise RuntimeError("expected 4 integers (zone, r, g, b)")
    try:
        zone, red, green, blue = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        raise RuntimeError("invalid integer") from None
    if not (0 <= red <= 255 and 0 <= green <= 255 and 0 <= blue <= 255):
        raise RuntimeError("rgb components must be 0-255")
    return zone, red, green, blue


def _parse_power_limits(body: str) -> tuple[int, int, int, int]:
    """Parse power-limit fields: stapm, fast, slow, tctl-temp (°C)."""
    parts = body.split("\t")
    if len(parts) != 4:
        raise RuntimeError("expected 4 integers (stapm, fast, slow, tctl)")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        raise RuntimeError("invalid integer") from None


def _parse_json_object(body: str, name: str) -> dict:
    if not body:
        raise RuntimeError(f"empty {name}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid {name} json") from e
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} must be a JSON object")
    return payload


def _make_dispatch(
    sampler: RaplPowerSampler,
    sampler_lock: threading.Lock | None,
    runtime: Runtime | None = None,
    peer: Peer | None = None,
):
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

    def _gpu_mux_mode(body: str) -> str:
        mode = _parse_int(body, "gpu-mux-mode")
        logger.info("[gpu-mux] daemon request: gpu-mux-mode %d", mode)
        try:
            result = sysfs.write_gpu_mux_mode(mode)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _fan_auto(_body: str) -> str:
        logger.info("[fan-control] daemon request: fan-auto")
        try:
            result = sysfs.write_pwm_enable(2)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _fan_max(_body: str) -> str:
        logger.info("[fan-control] daemon request: fan-max (pwm1_enable=0)")
        try:
            result = sysfs.write_pwm_max()
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
        # 3 fields: set all zones to the same RGB (single-zone / bulk path).
        # 4 fields: set one zone (zone, r, g, b).
        n = len(body.split("\t"))
        if n == 3:
            r, g, b = _parse_rgb(body)
            logger.info("[keyboard-rgb] daemon request: color %d %d %d", r, g, b)
            try:
                result = sysfs.write_keyboard_color(r, g, b)
            except RuntimeError as e:
                return protocol.format_status_response((False, str(e)))
            return protocol.format_status_response((True, result))
        if n == 4:
            zone, r, g, b = _parse_zone_rgb(body)
            logger.info(
                "[keyboard-rgb] daemon request: zone %d color %d %d %d",
                zone, r, g, b,
            )
            try:
                result = sysfs.write_keyboard_zone_color(zone, r, g, b)
            except RuntimeError as e:
                return protocol.format_status_response((False, str(e)))
            return protocol.format_status_response((True, result))
        raise RuntimeError("expected 3 integers (r g b) or 4 (zone r g b)")

    def _keyboard_brightness(body: str) -> str:
        level = _parse_int(body, "brightness")
        logger.info("[keyboard-rgb] daemon request: brightness %d/255", level)
        try:
            result = sysfs.write_keyboard_brightness(level)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _keyboard_user_brightness(body: str) -> str:
        level = _parse_int(body, "user-brightness")
        logger.info("[keyboard-rgb] daemon request: user-brightness %d/255", level)
        try:
            result = sysfs.set_keyboard_user_brightness(level)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _power_limits(body: str) -> str:
        s, f, sl, tctl = _parse_power_limits(body)
        logger.info(
            "[power-limits] daemon request: STAPM=%d fast=%d slow=%d tctl=%d",
            s, f, sl, tctl,
        )
        result = ryzenadj.apply_power_limits(s, f, sl, tctl)
        return protocol.format_status_response((True, result))

    def _intel_power_limits(body: str) -> str:
        parts = body.split("\t")
        if len(parts) != 2:
            raise RuntimeError("expected 2 integers (PL1, PL2 in mW)")
        result = intel.apply_power_limits(
            _parse_int(parts[0], "PL1"), _parse_int(parts[1], "PL2"),
        )
        return protocol.format_status_response((True, result))

    def _intel_undervolt(body: str) -> str:
        parts = body.split("\t")
        if len(parts) != 2:
            raise RuntimeError("expected 2 integers (core, cache in mV)")
        result = intel.apply_undervolt(
            _parse_int(parts[0], "core offset"), _parse_int(parts[1], "cache offset"),
        )
        return protocol.format_status_response((True, result))

    def _cpu_frequency_limits(body: str) -> str:
        parts = body.split("\t")
        if len(parts) != 2:
            raise RuntimeError("expected 2 integers (minimum, maximum in kHz)")
        minimum = _parse_int(parts[0], "minimum frequency")
        maximum = _parse_int(parts[1], "maximum frequency")
        result = cpufreq.apply_frequency_limits(minimum, maximum)
        return protocol.format_status_response((True, result))

    def _keyboard_last_input(_body: str) -> str:
        logger.info("[keyboard-rgb] daemon request: keyboard-last-input")
        elapsed = kbd_elapsed_since_last_input()
        return f"OK\t{elapsed:.3f}\n"

    def _require_runtime() -> Runtime:
        if runtime is None:
            raise RuntimeError("runtime is not running")
        return runtime

    def _fan_config(body: str) -> str:
        config = config_from_dict(_parse_json_object(body, "fan-config"))
        logger.info("[fan-control] daemon request: fan-config")
        result = _require_runtime().set_fan_config(config)
        return protocol.format_status_response((True, result))

    def _lighting_config(body: str) -> str:
        settings = lighting_from_dict(_parse_json_object(body, "lighting-config"))
        logger.info("[keyboard-rgb] daemon request: lighting-config")
        result = _require_runtime().set_lighting(settings)
        return protocol.format_status_response((True, result))

    def _power_config(body: str) -> str:
        policy = power_from_dict(_parse_json_object(body, "power-config"))
        logger.info("[power-limits] daemon request: power-config enabled=%s", policy.enabled)
        result = _require_runtime().set_power(policy)
        return protocol.format_status_response((True, result))

    def _cpu_frequency_config(body: str) -> str:
        body = body.lstrip("\t").strip()
        if not body:
            result = _require_runtime().set_cpu_frequency(None)
            return protocol.format_status_response((True, result))
        parts = body.split("\t")
        if len(parts) != 2:
            raise RuntimeError("expected 2 integers (minimum, maximum in kHz)")
        minimum = _parse_int(parts[0], "minimum frequency")
        maximum = _parse_int(parts[1], "maximum frequency")
        if not (0 < minimum <= maximum):
            raise RuntimeError("invalid frequency range")
        logger.info("[cpu-frequency] daemon request: persist %d-%d kHz", minimum, maximum)
        result = _require_runtime().set_cpu_frequency((minimum, maximum))
        return protocol.format_status_response((True, result))

    def _battery_power_save(body: str) -> str:
        enabled = _parse_int(body, "battery-power-save") != 0
        logger.info("[power] daemon request: battery-power-save %s", enabled)
        result = _require_runtime().set_battery_power_save(enabled)
        return protocol.format_status_response((True, result))

    def _hardware_shortcuts(body: str) -> str:
        enabled = _parse_int(body, "hardware-shortcuts") != 0
        logger.info("[keyboard] daemon request: hardware-shortcuts %s", enabled)
        result = _require_runtime().set_hardware_shortcuts(enabled)
        return protocol.format_status_response((True, result))

    def _program_shortcut(body: str) -> str:
        if peer is None:
            raise RuntimeError("a desktop user is required")
        payload = _parse_json_object(body, "program-shortcut")
        mods, key = validate_shortcut(payload.get("mods"), payload.get("key"))
        _require_runtime().program_shortcuts.set(peer.uid, mods, key)
        return protocol.format_status_response((True, "program-shortcut"))

    def _disable_nvidia_queries(body: str) -> str:
        enabled = _parse_int(body, "disable-nvidia-queries") != 0
        result = _require_runtime().set_disable_nvidia_queries(enabled)
        return protocol.format_status_response((True, result))

    def _set_profile(body: str) -> str:
        index = _parse_int(body, "profile")
        logger.info("[profile] daemon request: set-profile %d", index)
        try:
            result = _require_runtime().set_profile(index)
            return protocol.format_status_response((True, result))
        except RuntimeError as e:
            return protocol.format_status_response((False, str(e)))

    def _get_state(_body: str) -> str:
        from victus_hub.backend.hardware_capabilities import detect_capabilities

        state = state_to_dict(_require_runtime().snapshot())
        state["capabilities"] = detect_capabilities().to_dict()
        payload = json.dumps(
            state,
            separators=(",", ":"),
        )
        return f"OK\t{payload}\n"

    def _prepare_sleep(_body: str) -> str:
        logger.info("[power-state] daemon request: prepare-sleep")
        _require_runtime().prepare_sleep()
        return protocol.format_status_response((True, "prepare-sleep"))

    def _resume(_body: str) -> str:
        logger.info("[power-state] daemon request: resume")
        _require_runtime().resume()
        return protocol.format_status_response((True, "resume"))

    return [
        ("cpu-power", _cpu_power),
        ("gpu-mux-mode\t", _gpu_mux_mode),
        ("fan-config\t", _fan_config),
        ("fan-auto", _fan_auto),
        ("fan-max", _fan_max),
        ("fan-pwm\t", _fan_pwm),
        ("fan-manual", _fan_manual),
        ("lighting-config\t", _lighting_config),
        ("keyboard-color\t", _keyboard_color),
        ("power-config\t", _power_config),
        ("power-limits\t", _power_limits),
        ("intel-power-limits\t", _intel_power_limits),
        ("intel-undervolt\t", _intel_undervolt),
        ("cpu-frequency-config", _cpu_frequency_config),
        ("cpu-frequency-limits\t", _cpu_frequency_limits),
        ("battery-power-save\t", _battery_power_save),
        ("hardware-shortcuts\t", _hardware_shortcuts),
        ("program-shortcut\t", _program_shortcut),
        ("disable-nvidia-queries\t", _disable_nvidia_queries),
        ("set-profile\t", _set_profile),
        ("get-state", _get_state),
        ("prepare-sleep", _prepare_sleep),
        ("resume", _resume),
        ("keyboard-brightness\t", _keyboard_brightness),
        ("keyboard-user-brightness\t", _keyboard_user_brightness),
        ("keyboard-last-input", _keyboard_last_input),
    ]




def handle_client(
    stream: socket.socket,
    sampler: RaplPowerSampler,
    sampler_lock: threading.Lock | None = None,
    runtime: Runtime | None = None,
) -> None:
    """Handle one client connection. Runs in its own thread."""
    try:
        stream.settimeout(5)
        peer = authenticate(stream)
        _handle_request(stream, sampler, sampler_lock, runtime, peer)
    except (RuntimeError, UnicodeError) as error:
        try:
            stream.sendall(f"ERR\t{error}\n".encode())
        except OSError:
            pass
    finally:
        stream.close()


def _handle_request(stream, sampler, sampler_lock, runtime, peer):
    data = _recv_line(stream)
    if data is None:
        try:
            stream.sendall(b"ERR\tempty request\n")
        except OSError:
            pass
        return

    request = data.decode().rstrip("\n")
    if not peer.authorized():
        raise RuntimeError("access denied: session is no longer active and unlocked")
    if request in {"prepare-sleep", "resume"} and peer.uid != 0:
        raise RuntimeError("access denied: sleep hooks require root")
    if request == "shortcut-events":
        _stream_shortcut_events(stream, peer)
        return
    dispatch = _make_dispatch(sampler, sampler_lock, runtime, peer)
    matched_prefix: str | None = None
    handler = None
    for prefix, h in dispatch:
        if (prefix.endswith("\t") and request.startswith(prefix)) or request == prefix or (
            prefix == "cpu-frequency-config" and request.startswith(prefix + "\t")
        ):
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
    """Start the victus-hubd Unix socket daemon."""
    global _runtime
    socket_path = SOCKET_PATH

    # Clean up stale socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    os.makedirs("/run/victus-hubd", exist_ok=True, mode=0o755)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    # Connect permission is deliberately public; SO_PEERCRED + logind authorize
    # every request. No installation-time user group or stale session ACLs.
    os.chmod(socket_path, 0o666)
    server.listen(5)
    server.settimeout(1.0)

    sampler = RaplPowerSampler()
    # Use a lock because sampler.read() mutates self._sample
    sampler_lock = threading.Lock()

    start_keyboard_watcher()
    runtime = Runtime()
    runtime.set_idle_elapsed(kbd_elapsed_since_last_input)
    runtime.set_publish_lighting(_publish_lighting)
    _runtime = runtime
    runtime.start()

    stopping = threading.Event()

    def _on_signal(_signum, _frame) -> None:
        stopping.set()
        try:
            server.close()
        except OSError:
            pass

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info("victus-hubd listening on %s", socket_path)

    while not stopping.is_set():
        try:
            conn, _addr = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        def _handle(s: socket.socket):
            try:
                handle_client(s, sampler, sampler_lock, runtime)
            except Exception:
                logger.exception("client handler crashed")
            finally:
                s.close()
                _client_slots.release()

        if not _client_slots.acquire(blocking=False):
            conn.close()
            continue
        t = threading.Thread(target=_handle, args=(conn,), daemon=True)
        t.start()

    logger.info("victus-hubd shutting down")
    runtime.stop(reset_hardware=True)
