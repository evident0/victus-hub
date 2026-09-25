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


def _status(
    request: str,
    *,
    log: str | None = None,
    args: tuple = (),
    quiet: bool = False,
    missing_ok: bool = False,
    timeout: float = 1.0,
) -> str:
    if log is not None:
        logger.info(log, *args)
    kwargs = {}
    if quiet:
        kwargs["quiet"] = True
    if missing_ok:
        kwargs["missing_ok"] = True
    if timeout != 1.0:
        kwargs["timeout"] = timeout
    response = _request_daemon(request, **kwargs)
    return protocol.parse_status_response(response)


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
    return _status(
        f"gpu-mux-mode\t{mode}\n",
        log="\u2192 daemon: gpu-mux-mode %d",
        args=(mode,),
    )


def request_fan_auto() -> str:
    return _status("fan-auto\n", log="\u2192 daemon: fan-auto")


def request_fan_max() -> str:
    """Set pwm_enable=0 — BIOS/EC max-fan mode (hp-wmi PWM_MODE_MAX)."""
    return _status("fan-max\n", log="\u2192 daemon: fan-max")


def request_fan_manual() -> str:
    """Set pwm_enable=1 without writing a PWM value — use when entering manual mode."""
    return _status("fan-manual\n", log="\u2192 daemon: fan-manual")


def request_fan_pwm(pwm: int) -> str:
    pct = round(pwm / 255.0 * 100)
    return _status(
        f"fan-pwm\t{pwm}\n",
        log="\u2192 daemon: fan-pwm %d/255 (%d%%)",
        args=(pwm, pct),
    )


def request_keyboard_color(
    red: int, green: int, blue: int, zone: int | None = None,
) -> str:
    """Set keyboard color on all zones, or on one zone when *zone* is given."""
    if zone is None:
        return _status(
            f"keyboard-color\t{red}\t{green}\t{blue}\n",
            log="\u2192 daemon: keyboard-color %d %d %d",
            args=(red, green, blue),
        )
    return _status(
        f"keyboard-color\t{zone}\t{red}\t{green}\t{blue}\n",
        log="\u2192 daemon: keyboard-color zone %d %d %d %d",
        args=(zone, red, green, blue),
    )


def request_keyboard_brightness(level: int) -> str:
    return _status(
        f"keyboard-brightness\t{level}\n",
        log="\u2192 daemon: keyboard-brightness %d/255",
        args=(level,),
    )


def request_keyboard_user_brightness(level: int) -> str:
    """Set the user-preferred brightness (stored in the daemon for atomic color writes)."""
    return _status(
        f"keyboard-user-brightness\t{level}\n",
        log="\u2192 daemon: keyboard-user-brightness %d/255",
        args=(level,),
    )


def request_keyboard_last_input() -> float:
    """Return seconds since the last physical keypress on the laptop keyboard.

    The daemon emits `OK\t{elapsed:.3f}\\n` (see victus_hubd/daemon.py).
    """
    return float(_status("keyboard-last-input\n", quiet=True))


def request_cpu_frequency_limits(minimum: int, maximum: int) -> str:
    return _status(f"cpu-frequency-limits\t{minimum}\t{maximum}\n")


def request_intel_power_limits(pl1_mw: int, pl2_mw: int) -> str:
    return _status(f"intel-power-limits\t{pl1_mw}\t{pl2_mw}\n")


def request_intel_undervolt(core_mv: int, cache_mv: int) -> str:
    return _status(f"intel-undervolt\t{core_mv}\t{cache_mv}\n")


def request_power_limits(
    stapm_limit: int,
    fast_limit: int,
    slow_limit: int,
    tctl_temp: int = 95,
) -> str:
    return _status(
        f"power-limits\t{stapm_limit}\t{fast_limit}\t{slow_limit}\t{tctl_temp}\n",
        log="\u2192 daemon: power-limits STAPM=%d fast=%d slow=%d tctl=%d",
        args=(stapm_limit, fast_limit, slow_limit, tctl_temp),
    )


def _json_body(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def request_fan_config(config) -> str:
    from victus_hub.backend.fan_config import config_to_dict

    return _status(
        f"fan-config\t{_json_body(config_to_dict(config))}\n",
        log="\u2192 daemon: fan-config",
        missing_ok=True,
    )


def request_lighting_config(settings) -> str:
    from victus_hub.features.keyboard.lighting import lighting_to_dict

    return _status(
        f"lighting-config\t{_json_body(lighting_to_dict(settings))}\n",
        log="\u2192 daemon: lighting-config",
        missing_ok=True,
    )


def request_power_config(enabled: bool, settings) -> str:
    payload = {
        "enabled": bool(enabled),
        "stapm_limit": int(settings.stapm_limit),
        "fast_limit": int(settings.fast_limit),
        "slow_limit": int(settings.slow_limit),
        "tctl_temp": int(settings.tctl_temp),
        "reapply_seconds": int(settings.reapply_seconds),
    }
    return _status(
        f"power-config\t{_json_body(payload)}\n",
        log="\u2192 daemon: power-config enabled=%s",
        args=(enabled,),
        missing_ok=True,
    )


def request_cpu_frequency_config(minimum: int | None, maximum: int | None) -> str:
    if minimum is None or maximum is None:
        return _status(
            "cpu-frequency-config\n",
            log="\u2192 daemon: cpu-frequency-config clear",
            missing_ok=True,
        )
    return _status(
        f"cpu-frequency-config\t{minimum}\t{maximum}\n",
        log="\u2192 daemon: cpu-frequency-config %d-%d",
        args=(minimum, maximum),
        missing_ok=True,
    )


def request_battery_power_save(enabled: bool) -> str:
    return _status(
        f"battery-power-save\t{1 if enabled else 0}\n",
        log="\u2192 daemon: battery-power-save %s",
        args=(enabled,),
        missing_ok=True,
    )


def request_hardware_shortcuts(enabled: bool) -> str:
    return _status(
        f"hardware-shortcuts\t{1 if enabled else 0}\n",
        log="\u2192 daemon: hardware-shortcuts %s",
        args=(enabled,),
        missing_ok=True,
    )


def request_program_shortcut(mods: tuple[int, ...], key: int) -> str:
    return _status(
        f"program-shortcut\t{_json_body({'mods': mods, 'key': key})}\n",
        missing_ok=True,
    )


def request_disable_nvidia_queries(enabled: bool) -> str:
    return _status(
        f"disable-nvidia-queries\t{int(enabled)}\n",
        missing_ok=True,
    )


def request_get_state() -> dict:
    payload = _status("get-state\n", missing_ok=True)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("get-state did not return a JSON object")
    return data
