"""Unprivileged socket client to victus-hubd daemon.

Ports privileged/client.rs exactly.
"""

import json
import logging
import socket

from victus_hub.backend import protocol
from victus_hub.backend.rapl import CpuPowerSample
from victus_hub.backend.types import SensorSnapshot
from victus_hub.logging_config import message_debug_level

SOCKET_PATH = "/run/victus-hubd/victus-hub.sock"

# ANSI red for error responses (most terminals support colors).
_RED = "\033[31m"
_RESET = "\033[0m"

logger = logging.getLogger(__name__)


def _request_daemon(
    request: str,
    quiet: bool = False,
    *,
    missing_ok: bool = False,
    timeout: float = 1.0,
) -> str:
    """Send one line to the daemon and read one line back."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall(request.encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        resp = response.decode().strip()
        if resp.startswith("ERR"):
            logger.error("%s\u2190 daemon: %s%s", _RED, resp, _RESET)
        elif not quiet:
            logger.info(
                "\u2190 daemon: %s", resp,
                extra={"debug_level": message_debug_level(request)},
            )
        return resp
    except OSError as e:
        if missing_ok:
            raise RuntimeError(str(e))
        logger.error("%s\u2190 daemon: ERROR %s%s", _RED, e, _RESET)
        raise RuntimeError(str(e))
    finally:
        sock.close()

def request_sensors(keys, *, timeout: float = 6.0) -> SensorSnapshot:
    """Ask the daemon to read exactly these informational sensors."""
    body = ",".join(sorted(frozenset(keys)))
    request = "sensors\n" if not body else f"sensors\t{body}\n"
    # lm-sensors on the Sensors page can take a few seconds. Other pages do not ask for it.
    response = _request_daemon(request, quiet=True, timeout=timeout)
    return protocol.parse_sensors_response(response)


def request_cpu_power() -> CpuPowerSample:
    response = _request_daemon("cpu-power\n", quiet=True)
    return protocol.parse_cpu_power_response(response)


def request_gpu_mux_mode(mode: int) -> str:
    logger.info("\u2192 daemon: gpu-mux-mode %d", mode)
    response = _request_daemon(f"gpu-mux-mode\t{mode}\n")
    return protocol.parse_status_response(response)


def request_fan_auto() -> str:
    logger.info("\u2192 daemon: fan-auto")
    response = _request_daemon("fan-auto\n")
    return protocol.parse_status_response(response)


def request_fan_max() -> str:
    """Set pwm_enable=0 — BIOS/EC max-fan mode (hp-wmi PWM_MODE_MAX)."""
    logger.info("\u2192 daemon: fan-max")
    response = _request_daemon("fan-max\n")
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


def request_keyboard_color(
    red: int, green: int, blue: int, zone: int | None = None,
) -> str:
    """Set keyboard color on all zones, or on one zone when *zone* is given."""
    if zone is None:
        logger.info("\u2192 daemon: keyboard-color %d %d %d", red, green, blue)
        response = _request_daemon(f"keyboard-color\t{red}\t{green}\t{blue}\n")
    else:
        logger.info(
            "\u2192 daemon: keyboard-color zone %d %d %d %d",
            zone, red, green, blue,
        )
        response = _request_daemon(
            f"keyboard-color\t{zone}\t{red}\t{green}\t{blue}\n",
        )
    return protocol.parse_status_response(response)

def request_keyboard_brightness(level: int) -> str:
    logger.info("\u2192 daemon: keyboard-brightness %d/255", level)
    response = _request_daemon(f"keyboard-brightness\t{level}\n")
    return protocol.parse_status_response(response)

def request_keyboard_user_brightness(level: int) -> str:
    """Set the user-preferred brightness (stored in the daemon for atomic color writes)."""
    logger.info("\u2192 daemon: keyboard-user-brightness %d/255", level)
    response = _request_daemon(f"keyboard-user-brightness\t{level}\n")
    return protocol.parse_status_response(response)


def request_keyboard_last_input() -> float:
    """Return seconds since the last physical keypress on the laptop keyboard.

    The daemon emits `OK\t{elapsed:.3f}\\n` (see victus_hubd/daemon.py).
    """
    response = _request_daemon("keyboard-last-input\n", quiet=True)
    return float(protocol.parse_status_response(response))


def request_cpu_frequency_limits(minimum: int, maximum: int) -> str:
    response = _request_daemon(f"cpu-frequency-limits\t{minimum}\t{maximum}\n")
    return protocol.parse_status_response(response)


def request_intel_power_limits(pl1_mw: int, pl2_mw: int) -> str:
    response = _request_daemon(f"intel-power-limits\t{pl1_mw}\t{pl2_mw}\n")
    return protocol.parse_status_response(response)


def request_intel_undervolt(core_mv: int, cache_mv: int) -> str:
    response = _request_daemon(f"intel-undervolt\t{core_mv}\t{cache_mv}\n")
    return protocol.parse_status_response(response)


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
        f"power-limits\t{stapm_limit}\t{fast_limit}\t{slow_limit}\t{tctl_temp}\n",
    )
    return protocol.parse_status_response(response)


def _json_body(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def request_fan_config(config) -> str:
    from victus_hub.backend.fan_config import config_to_dict

    logger.info("\u2192 daemon: fan-config")
    response = _request_daemon(
        f"fan-config\t{_json_body(config_to_dict(config))}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_lighting_config(settings) -> str:
    from victus_hub.features.keyboard.lighting import lighting_to_dict

    logger.info("\u2192 daemon: lighting-config")
    response = _request_daemon(
        f"lighting-config\t{_json_body(lighting_to_dict(settings))}\n",
        missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_power_config(enabled: bool, settings) -> str:
    payload = {
        "enabled": bool(enabled),
        "stapm_limit": int(settings.stapm_limit),
        "fast_limit": int(settings.fast_limit),
        "slow_limit": int(settings.slow_limit),
        "tctl_temp": int(settings.tctl_temp),
        "reapply_seconds": int(settings.reapply_seconds),
    }
    logger.info("\u2192 daemon: power-config enabled=%s", enabled)
    response = _request_daemon(
        f"power-config\t{_json_body(payload)}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_cpu_frequency_config(minimum: int | None, maximum: int | None) -> str:
    if minimum is None or maximum is None:
        logger.info("\u2192 daemon: cpu-frequency-config clear")
        response = _request_daemon("cpu-frequency-config\n", missing_ok=True)
        return protocol.parse_status_response(response)
    logger.info("\u2192 daemon: cpu-frequency-config %d-%d", minimum, maximum)
    response = _request_daemon(
        f"cpu-frequency-config\t{minimum}\t{maximum}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_battery_power_save(enabled: bool) -> str:
    logger.info("\u2192 daemon: battery-power-save %s", enabled)
    response = _request_daemon(
        f"battery-power-save\t{1 if enabled else 0}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_hardware_shortcuts(enabled: bool) -> str:
    logger.info("\u2192 daemon: hardware-shortcuts %s", enabled)
    response = _request_daemon(
        f"hardware-shortcuts\t{1 if enabled else 0}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_program_shortcut(mods: tuple[int, ...], key: int) -> str:
    response = _request_daemon(
        f"program-shortcut\t{_json_body({'mods': mods, 'key': key})}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_disable_nvidia_queries(enabled: bool) -> str:
    response = _request_daemon(
        f"disable-nvidia-queries\t{int(enabled)}\n", missing_ok=True,
    )
    return protocol.parse_status_response(response)


def request_get_state() -> dict:
    response = _request_daemon("get-state\n", missing_ok=True)
    payload = protocol.parse_status_response(response)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("get-state did not return a JSON object")
    return data
