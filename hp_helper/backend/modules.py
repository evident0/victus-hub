"""Hardware module / driver presence detection.

Each function returns ``(text, color)`` suitable for a ``StatusBadge``.
"""

from pathlib import Path

from hp_helper.app.theme import COLORS
from hp_helper.backend.sysfs_read import find_hwmon_by_name

GREEN = COLORS["accent_green"]
ORANGE = "#f0a030"
RED = COLORS["accent_red"]


def platform_profile_backend() -> tuple[str, str]:
    """Detect which platform-profile backend is available.

    Returns ``(label, color)`` — e.g. ``("tuned", GREEN)`` or ``("none", RED)``.
    """
    from hp_helper.backend.profiles import tuned_adm_path
    from hp_helper.backend.util import command_path

    if tuned_adm_path() is not None:
        return ("tuned", GREEN)
    if command_path("powerprofilesctl") is not None:
        return ("ppd", GREEN)
    return ("none", RED)


def fan_control_module() -> tuple[str, str]:
    """Detect whether hp-wmi exposes fan control.

    - Green ``"hp-wmi"`` — hwmon found and pwm1_enable exists (custom fan control)
    - Orange ``"limited"`` — hwmon found but pwm1_enable missing (only auto / max)
    - Red ``"none"`` — no hp-wmi hwmon at all
    """
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    if hwmon is None:
        return ("none", RED)
    if (hwmon / "pwm1_enable").exists():
        return ("hp-wmi", GREEN)
    return ("limited", ORANGE)


def mux_module() -> tuple[str, str]:
    """Detect whether the hp-gpu-mux kernel module is loaded.

    - Green ``"mux"`` if /sys/devices/platform/hp-gpu-mux exists
    - Red ``"no mux"`` otherwise
    """
    if Path("/sys/devices/platform/hp-gpu-mux").is_dir():
        return ("hp-gpu-mux", GREEN)
    return ("no mux", RED)


def keyboard_rgb_module() -> tuple[str, str]:
    """Detect whether the hp-kbd-rgb kernel module is loaded.

    - Green ``"hp-kbd-rgb"`` if /sys/devices/platform/hp-kbd-rgb exists
    - Red ``"no kbd"`` otherwise
    """
    if Path("/sys/devices/platform/hp-kbd-rgb").is_dir():
        return ("hp-kbd-rgb", GREEN)
    return ("no kbd", RED)
