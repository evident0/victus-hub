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
    req = request.strip()
    logger.info("→ daemon: %s", req)
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
        logger.info("← daemon: %s", resp)
        return response.decode()
    except OSError as e:
        logger.info("← daemon: ERROR %s", e)
        raise RuntimeError(str(e))


def request_cpu_power() -> CpuPowerSample:
    response = _request_daemon("cpu-power\n")
    return protocol.parse_cpu_power_response(response)


def request_fan_auto() -> str:
    response = _request_daemon("fan-auto\n")
    return protocol.parse_status_response(response)


def request_fan_pwm(pwm: int, cpu_avg_c: float | None, gpu_avg_c: float | None) -> str:
    response = _request_daemon(f"fan-pwm\t{pwm}\n")
    return protocol.parse_status_response(response)


def request_keyboard_color(red: int, green: int, blue: int) -> str:
    response = _request_daemon(f"keyboard-color\t{red}\t{green}\t{blue}\n")
    return protocol.parse_status_response(response)


def request_power_limits(stapm_limit: int, fast_limit: int, slow_limit: int) -> str:
    response = _request_daemon(
        f"power-limits\t{stapm_limit}\t{fast_limit}\t{slow_limit}\n"
    )
    return protocol.parse_status_response(response)
