"""System-level sysfs writes for HP hardware; ports privileged/sysfs.rs.

This module requires root privileges for most operations.
"""
import logging
from pathlib import Path

from hp_helper.backend.sysfs_read import find_hwmon_by_name


KBD_RGB_PLATFORM = "/sys/devices/platform/hp-kbd-rgb"
KBD_RGB_LEDS = "/sys/class/leds"

logger = logging.getLogger(__name__)


def hp_hwmon() -> Path | None:
    """Find the HP hwmon directory."""
    return find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")




def write_sysfs(path: Path, value: int | str) -> str:
    """Write a value to a sysfs path; returns path label on success."""
    label = str(path)
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


def _kbd_rgb_led_names() -> list[str]:
    """Return the LED class device basenames for the keyboard zones.

    Reads zone_count from the platform device and maps it to the LED
    names registered by the module:
      - single-zone (1): hp::kbd_backlight
      - 4-zone (4):      hp::kbd_backlight_zoned_backlight-{right,center,left,wasd}
    """
    zc_path = Path(KBD_RGB_PLATFORM) / "zone_count"
    try:
        zone_count = int(zc_path.read_text().strip())
    except (OSError, ValueError):
        zone_count = 1

    if zone_count == 1:
        return ["hp::kbd_backlight"]
    return [
        "hp::kbd_backlight_zoned_backlight-right",
        "hp::kbd_backlight_zoned_backlight-center",
        "hp::kbd_backlight_zoned_backlight-left",
        "hp::kbd_backlight_zoned_backlight-wasd",
    ][:zone_count]


def write_keyboard_color(red: int, green: int, blue: int) -> str:
    """Set the keyboard backlight color via the LED multicolor interface.

    Writes ``multi_intensity`` on every zone's LED class device, then
    sets ``brightness`` to 255 so the backlight turns on at full
    intensity with the requested color.  This keeps the LED subsystem
    state in sync with the hardware (unlike writing the legacy platform
    ``color`` node, which desynced brightness from color).
    """
    value = f"{red} {green} {blue}"
    labels: list[str] = []
    for name in _kbd_rgb_led_names():
        led = Path(KBD_RGB_LEDS) / name
        write_sysfs(led / "multi_intensity", value)
        write_sysfs(led / "brightness", 255)
        labels.append(str(led))
    logger.info("[keyboard-rgb] color %s -> %s", value, ", ".join(labels))
    return labels[0] if labels else KBD_RGB_LEDS


def write_keyboard_brightness(level: int) -> str:
    """Set keyboard backlight brightness (0-255) on all zones.

    Writing 0 turns the backlight off (sends black via the LED multicolor
    scaling path); writing 255 restores full intensity at the currently
    stored color.
    """
    level = max(0, min(255, level))
    labels: list[str] = []
    for name in _kbd_rgb_led_names():
        led = Path(KBD_RGB_LEDS) / name
        write_sysfs(led / "brightness", level)
        labels.append(str(led))
    logger.info("[keyboard-rgb] brightness %d -> %s", level, ", ".join(labels))
    return labels[0] if labels else KBD_RGB_LEDS


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


def find_wmi_hotkeys_device() -> str | None:
    """Return the /dev/input/eventX path for the "HP WMI hotkeys" device.

    The OMEN key on HP laptops surfaces here (as a normal keycode such as
    KEY_PROG2), not on the i8042 AT keyboard device, so the daemon must
    watch this device too to detect it.
    """
    base = Path("/sys/class/input")
    try:
        entries = list(base.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.startswith("input"):
            continue
        try:
            name = (entry / "name").read_text().strip()
        except OSError:
            continue
        if name != "HP WMI hotkeys":
            continue
        for child in entry.iterdir():
            if not child.name.startswith("event"):
                continue
            dev = Path("/dev/input") / child.name
            if dev.exists():
                return str(dev)
    return None
