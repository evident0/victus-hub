"""Unprivileged socket client to hp-helperd daemon.

Ports privileged/client.rs exactly.
"""

import logging
import socket

from hp_helper.backend import protocol
from hp_helper.backend.rapl import CpuPowerSample

SOCKET_PATH = "/run/hp-helperd/hp-helper-rs.sock"

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)


def _request_daemon(request: str) -> str:
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
        logger.info("\u2190 daemon: %s", resp)
        return response.decode()
    except OSError as e:
        logger.info("\u2190 daemon: ERROR %s", e)
        raise RuntimeError(str(e))


def request_cpu_power() -> CpuPowerSample:
    logger.info("\u2192 daemon: cpu-power")
    response = _request_daemon("cpu-power\n")
    return protocol.parse_cpu_power_response(response)


def request_fan_auto() -> str:
    logger.info("\u2192 daemon: fan-auto")
    response = _request_daemon("fan-auto\n")
    return protocol.parse_status_response(response)


def request_fan_pwm(pwm: int, cpu_avg_c: float | None, gpu_avg_c: float | None) -> str:
    pct = round(pwm / 255.0 * 100)
    logger.info("\u2192 daemon: fan-pwm %d/255 (%d%%)", pwm, pct)
    response = _request_daemon(f"fan-pwm\t{pwm}\n")
    return protocol.parse_status_response(response)


def request_keyboard_color(red: int, green: int, blue: int) -> str:
    logger.info("\u2192 daemon: keyboard-color %d %d %d", red, green, blue)
    response = _request_daemon(f"keyboard-color\t{red}\t{green}\t{blue}\n")
    return protocol.parse_status_response(response)


def request_power_limits(stapm_limit: int, fast_limit: int, slow_limit: int) -> str:
    logger.info("\u2192 daemon: power-limits STAPM=%d fast=%d slow=%d", stapm_limit, fast_limit, slow_limit)
    response = _request_daemon(f"power-limits\t{stapm_limit}\t{fast_limit}\t{slow_limit}\n")
    return protocol.parse_status_response(response)
