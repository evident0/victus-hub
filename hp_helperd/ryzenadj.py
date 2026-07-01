"""ryzenadj power-limit control; ports privileged/ryzenadj.rs.

Requires the `ryzenadj` binary installed and root privileges.
"""

import logging
import os
import subprocess
from pathlib import Path

MIN_LIMIT_MW = 1_000
MAX_LIMIT_MW = 100_000

logger = logging.getLogger(__name__)


def _validate_limit(name: str, value: int) -> None:
    if MIN_LIMIT_MW <= value <= MAX_LIMIT_MW:
        return
    raise RuntimeError(f"{name} must be between {MIN_LIMIT_MW} and {MAX_LIMIT_MW} mW")


def _ryzenadj_path() -> Path | None:
    env = os.environ.get("RYZENADJ_PATH")
    if env:
        p = Path(env)
        if p.is_file():
            return p

    for candidate in ["/usr/local/bin/ryzenadj", "/usr/bin/ryzenadj"]:
        p = Path(candidate)
        if p.is_file():
            return p

    # PATH search
    for dir_entry in os.environ.get("PATH", "").split(":"):
        if not dir_entry:
            continue
        p = Path(dir_entry) / "ryzenadj"
        if p.is_file():
            return p
    return None


def apply_power_limits(stapm_limit: int, fast_limit: int, slow_limit: int) -> str:
    _validate_limit("stapm-limit", stapm_limit)
    _validate_limit("fast-limit", fast_limit)
    _validate_limit("slow-limit", slow_limit)

    exe = _ryzenadj_path()
    if exe is None:
        raise RuntimeError(
            "ryzenadj not found; install it as /usr/local/bin/ryzenadj or set RYZENADJ_PATH"
        )

    args = [
        str(exe),
        f"--stapm-limit={stapm_limit}",
        f"--fast-limit={fast_limit}",
        f"--slow-limit={slow_limit}",
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    except OSError as e:
        raise RuntimeError(f"failed to run {exe}: {e}")

    if result.returncode == 0:
        return f"applied STAPM {stapm_limit} mW, fast {fast_limit} mW, slow {slow_limit} mW"

    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    message = stderr if stderr else stdout
    if not message:
        message = f"{exe} exited with {result.returncode}"
    raise RuntimeError(message)
