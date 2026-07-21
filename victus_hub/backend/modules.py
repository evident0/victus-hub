"""Hardware module / driver presence detection.

Each function returns ``(text, color)`` suitable for a ``StatusBadge``.
"""

from pathlib import Path

import os

from victus_hub.backend.sysfs_read import find_hwmon_by_name
from victus_hub.app.theme import COLORS

GREEN = COLORS["accent_green"]
ORANGE = "#f0a030"
RED = COLORS["accent_red"]


def platform_profile_backend() -> tuple[str, str]:
    """Detect which platform-profile backend is available.

    Returns ``(label, color)`` — e.g. ``("tuned", GREEN)`` or ``("not supported", RED)``.
    """
    from victus_hub.backend.profiles import tuned_adm_path
    from victus_hub.backend.util import command_path

    if tuned_adm_path() is not None:
        return ("tuned", GREEN)
    if command_path("powerprofilesctl") is not None:
        return ("ppd", GREEN)
    return ("not supported", RED)


def fan_control_module() -> tuple[str, str]:
    """Detect whether hp-wmi exposes fan control.

    - Green ``"hp-wmi"`` — hwmon found and pwm1_enable exists (custom fan control)
    - Orange ``"limited"`` — hwmon found but pwm1_enable missing (only auto / max)
    - Red ``"not supported"`` — no hp-wmi hwmon at all
    """
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    if hwmon is None:
        return ("not supported", RED)
    if (hwmon / "pwm1_enable").exists():
        return ("hp-wmi", GREEN)
    return ("limited", ORANGE)


def mux_module() -> tuple[str, str]:
    """Detect whether the hp-gpu-mux kernel module is loaded.

    - Green ``"mux"`` if /sys/devices/platform/hp-gpu-mux exists
    - Red ``"not supported"`` otherwise
    """
    if Path("/sys/devices/platform/hp-gpu-mux").is_dir():
        return ("hp-gpu-mux", GREEN)
    return ("not supported", RED)


def keyboard_rgb_module() -> tuple[str, str]:
    """Detect whether the hp-kbd-rgb kernel module is loaded.

    - Green ``"hp-kbd-rgb"`` if /sys/devices/platform/hp-kbd-rgb exists
    - Red ``"not supported"`` otherwise
    """
    if Path("/sys/devices/platform/hp-kbd-rgb").is_dir():
        return ("hp-kbd-rgb", GREEN)
    return ("not supported", RED)


def ryzenadj_available() -> tuple[str, str]:
    """Detect whether the ``ryzenadj`` binary is available for APU power limits.

    - Green ``"ryzenadj"`` if the binary is found
    - Red ``"not supported"`` otherwise
    """
    # RYZENADJ_PATH env var
    env = os.environ.get("RYZENADJ_PATH")
    if env and Path(env).is_file():
        return ("ryzenadj", GREEN)
    # Standard install paths
    for candidate in ["/usr/local/bin/ryzenadj", "/usr/bin/ryzenadj"]:
        if Path(candidate).is_file():
            return ("ryzenadj", GREEN)
    # PATH search
    from victus_hub.backend.util import command_path
    if command_path("ryzenadj") is not None:
        return ("ryzenadj", GREEN)
    return ("not supported", RED)
