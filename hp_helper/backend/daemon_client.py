"""Unprivileged socket client to hp-helperd daemon.

Ports privileged/client.rs exactly.
"""

import logging
import socket

from hp_helper.backend import protocol
from hp_helper.backend.rapl import CpuPowerSample

SOCKET_PATH = "/run/hp-helperd/hp-helper-rs.sock"

# ANSI red for error responses (most terminals support colors).
_RED = "\033[31m"
_RESET = "\033[0m"

logger = logging.getLogger(__name__)


def _request_daemon(request: str, quiet: bool = False) -> str:
    """Send one line to the daemon and read one line back."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(SOCKET_PATH)
        sock.sendall(request.encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        sock.close()
        resp = response.decode().strip()
        if not quiet:
            if resp.startswith("ERR"):
                logger.info("%s\u2190 daemon: %s%s", _RED, resp, _RESET)
            else:
                logger.info("\u2190 daemon: %s", resp)
        return resp
    except OSError as e:
        logger.info("%s\u2190 daemon: ERROR %s%s", _RED, e, _RESET)
        raise RuntimeError(str(e))

def request_cpu_power() -> CpuPowerSample:
    response = _request_daemon("cpu-power\n", quiet=True)
    return protocol.parse_cpu_power_response(response)


def request_fan_auto() -> str:
    logger.info("\u2192 daemon: fan-auto")
    response = _request_daemon("fan-auto\n")
    return protocol.parse_status_response(response)

def request_fan_manual() -> str:
    """Set pwm_enable=1 without writing a PWM value — use when entering manual mode."""
    logger.info("\u2192 daemon: fan-manual")
    response = _request_daemon("fan-manual\n")
    return protocol.parse_status_response(response)


def request_fan_pwm(pwm: int) -> str:
    pct = round(pwm / 255.0 * 100)
    logger.info("\u2192 daemon: fan-pwm %d/255 (%d%%)", pwm, pct)
    response = _request_daemon(f"fan-pwm\t{pwm}\n")
    return protocol.parse_status_response(response)


def request_keyboard_color(red: int, green: int, blue: int) -> str:
    logger.info("\u2192 daemon: keyboard-color %d %d %d", red, green, blue)
    response = _request_daemon(f"keyboard-color\t{red}\t{green}\t{blue}\n")
    return protocol.parse_status_response(response)

def request_keyboard_brightness(level: int) -> str:
    logger.info("\u2192 daemon: keyboard-brightness %d/255", level)
    response = _request_daemon(f"keyboard-brightness\t{level}\n")
    return protocol.parse_status_response(response)


def request_keyboard_last_input() -> float:
    """Return seconds since the last physical keypress on the laptop keyboard.

    The daemon emits `OK\t{elapsed:.3f}\\n` (see hp_helperd/daemon.py).
    """
    response = _request_daemon("keyboard-last-input\n", quiet=True)
    return float(protocol.parse_status_response(response))


def request_keyboard_last_event() -> tuple[list[int], int, int]:
    """Return the last non-modifier keypress: (mods, key, seq).

    The daemon emits ``OK\\t{mods_csv}\\t{key}\\t{seq}\\n`` (see
    hp_helperd/daemon.py).  ``mods_csv`` is a comma-separated list of
    modifier keycodes held at press time, ``key`` is the non-modifier
    keycode (0 if none), and ``seq`` is a monotonic counter that bumps on
    every recorded press so callers can detect a fresh event.
    """
    response = _request_daemon("keyboard-last-event\n", quiet=True)
    line = response.strip()
    parts = line.split("\t")
    if len(parts) < 4 or parts[0] != "OK":
        raise RuntimeError(f"unexpected keyboard-last-event response: {line}")
    mods_csv = parts[1]
    mods = [int(x) for x in mods_csv.split(",") if x]
    return mods, int(parts[2]), int(parts[3])


def request_power_limits(
    stapm_limit: int,
    fast_limit: int,
    slow_limit: int,
    tctl_temp: int = 95,
) -> str:
    logger.info(
        "\u2192 daemon: power-limits STAPM=%d fast=%d slow=%d tctl=%d",
        stapm_limit, fast_limit, slow_limit, tctl_temp,
    )
    response = _request_daemon(
        f"power-limits\t{stapm_limit}\t{fast_limit}\t{slow_limit}\t{tctl_temp}\n"
    )
    return protocol.parse_status_response(response)
