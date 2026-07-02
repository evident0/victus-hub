"""System-level sysfs writes for HP hardware; ports privileged/sysfs.rs.

This module requires root privileges for most operations.
"""

import logging
from pathlib import Path

from hp_helper.backend.util import path_label

KBD_RGB_COLOR_PATH = "/sys/devices/platform/hp-kbd-rgb/color"

logger = logging.getLogger(__name__)


def hp_hwmon() -> Path | None:
    """Find the HP hwmon directory (duplicated from backend/sensors.py)."""
    hwmon_root = Path("/sys/class/hwmon")
    try:
        dirs = sorted(
            entry for entry in hwmon_root.iterdir()
            if entry.name.startswith("hwmon")
        )
    except OSError:
        return None
    for hwmon in dirs:
        try:
            name = (hwmon / "name").read_text().strip().lower()
        except OSError:
            continue
        if name in ("hp", "hp_wmi", "hp-wmi"):
            return hwmon
    return None


def write_sysfs(path: Path, value: int | str) -> str:
    """Write a value to a sysfs path; returns path label on success."""
    label = path_label(path)
    try:
        path.write_text(str(value))
    except OSError as e:
        logger.error("%s=%s: %s", label, value, e)
        raise RuntimeError(f"{label}: {e}")
    logger.info("%s=%s", label, value)
    return label


def write_pwm_enable(mode: int) -> str:
    """Set fan PWM mode (0=Max, 1=Manual, 2=Automatic)."""
    hwmon = hp_hwmon()
    if hwmon is None:
        raise RuntimeError("hp hwmon not found")
    logger.info("[fan-control] daemon setting pwm1_enable=%d", mode)
    return write_sysfs(hwmon / "pwm1_enable", mode)


def write_pwm(pwm: int) -> str:
    """Set fan PWM duty cycle (0-255). Enters manual mode first."""
    hwmon = hp_hwmon()
    if hwmon is None:
        raise RuntimeError("hp hwmon not found")
    enable_path = hwmon / "pwm1_enable"
    logger.info("[fan-control] daemon entering manual mode: pwm1_enable=1")
    write_sysfs(enable_path, 1)
    logger.info("[fan-control] daemon setting pwm1=%d", pwm)
    return write_sysfs(hwmon / "pwm1", pwm)


def write_keyboard_color(red: int, green: int, blue: int) -> str:
    """Write RGB color to the keyboard backlight sysfs node."""
    value = f"{red} {green} {blue}"
    path = Path(KBD_RGB_COLOR_PATH)
    try:
        path.write_text(value)
    except OSError as e:
        logger.error(
            "[keyboard-rgb] daemon sysfs write failed: %s=%s: %s",
            KBD_RGB_COLOR_PATH, value, e,
        )
        raise RuntimeError(f"{KBD_RGB_COLOR_PATH}: {e}")
    logger.info(
        "[keyboard-rgb] daemon sysfs write ok: %s=%s",
        KBD_RGB_COLOR_PATH, value,
    )
    return KBD_RGB_COLOR_PATH


# ── Keyboard input device ──

_KBD_BY_PATH = "/dev/input/by-path/platform-i8042-serio-0-event-kbd"


def find_laptop_keyboard_device() -> str | None:
    """Return the path to the built-in laptop keyboard input device.

    Looks for the i8042 (AT keyboard controller) event device which
    is the internal laptop keyboard, excluding external USB keyboards.
    """
    if Path(_KBD_BY_PATH).exists():
        return _KBD_BY_PATH
    # Fallback: search for any i8042 keyboard device
    for child in Path("/dev/input/by-path").iterdir():
        name = child.name
        if "i8042" in name and name.endswith("-event-kbd"):
            return str(child)
    return None
