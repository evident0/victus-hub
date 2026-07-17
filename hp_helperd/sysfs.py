"""System-level sysfs writes for HP hardware; ports privileged/sysfs.rs.

This module requires root privileges for most operations.
"""
import logging
from pathlib import Path

from hp_helper.backend.sysfs_read import find_hwmon_by_name


KBD_RGB_PLATFORM = "/sys/devices/platform/hp-kbd-rgb"
KBD_RGB_LEDS = "/sys/class/leds"
GPU_MUX_PLATFORM = Path("/sys/devices/platform/hp-gpu-mux")

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


def write_gpu_mux_mode(mode: int) -> str:
    """Request a GPU MUX mode index (0=hybrid, 1=discrete, 2=optimus, 3=uma)."""
    path = GPU_MUX_PLATFORM / "gpu_mux_mode"
    if not path.exists():
        raise RuntimeError("hp-gpu-mux platform device not found")
    return write_sysfs(path, mode)


def write_pwm_enable(mode: int) -> str:
    """Set fan PWM mode (0=Max, 1=Manual, 2=Automatic).

    Mode 0 is the BIOS/EC max-fan path (hp-wmi PWM_MODE_MAX /
    HPWMI_FAN_SPEED_MAX_SET_QUERY). Manual duty is mode 1 + pwm1.
    """
    hwmon = hp_hwmon()
    if hwmon is None:
        raise RuntimeError("hp hwmon not found")
    logger.info("[fan-control] daemon setting pwm1_enable=%d", mode)
    return write_sysfs(hwmon / "pwm1_enable", mode)


def write_pwm_max() -> str:
    """Engage BIOS max-fan mode (pwm1_enable=0)."""
    return write_pwm_enable(0)


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


def get_keyboard_zone_count() -> int:
    """Return zone count from the platform device (1 or 4). Defaults to 1."""
    zc_path = Path(KBD_RGB_PLATFORM) / "zone_count"
    try:
        zone_count = int(zc_path.read_text().strip())
    except (OSError, ValueError):
        return 1
    if zone_count < 1:
        return 1
    return zone_count


def _kbd_rgb_led_names() -> list[str]:
    """Return the LED class device basenames for the keyboard zones.

    Reads zone_count from the platform device and maps it to the LED
    names registered by the module:
      - single-zone (1): hp::kbd_backlight
      - 4-zone (4):      hp::kbd_backlight_zoned_backlight-{right,center,left,wasd}
    """
    zone_count = get_keyboard_zone_count()

    if zone_count == 1:
        return ["hp::kbd_backlight"]
    return [
        "hp::kbd_backlight_zoned_backlight-right",
        "hp::kbd_backlight_zoned_backlight-center",
        "hp::kbd_backlight_zoned_backlight-left",
        "hp::kbd_backlight_zoned_backlight-wasd",
    ][:zone_count]


def _write_led_color(led: Path, red: int, green: int, blue: int) -> str:
    """Write multi_intensity + full brightness for one LED class device."""
    value = f"{red} {green} {blue}"
    write_sysfs(led / "multi_intensity", value)
    write_sysfs(led / "brightness", 255)
    return str(led)


def write_keyboard_color(red: int, green: int, blue: int) -> str:
    """Set the same keyboard backlight color on every zone.

    Writes ``multi_intensity`` on every zone's LED class device, then
    sets ``brightness`` to 255 so the backlight turns on at full
    intensity with the requested color.  This keeps the LED subsystem
    state in sync with the hardware.
    """
    value = f"{red} {green} {blue}"
    labels: list[str] = []
    for name in _kbd_rgb_led_names():
        labels.append(_write_led_color(Path(KBD_RGB_LEDS) / name, red, green, blue))
    logger.info("[keyboard-rgb] color %s -> %s", value, ", ".join(labels))
    return labels[0] if labels else KBD_RGB_LEDS


def write_keyboard_zone_color(zone: int, red: int, green: int, blue: int) -> str:
    """Set color for a single zone (0-based index into LED name list)."""
    names = _kbd_rgb_led_names()
    if zone < 0 or zone >= len(names):
        raise RuntimeError(f"zone {zone} out of range (0-{max(0, len(names) - 1)})")
    led = Path(KBD_RGB_LEDS) / names[zone]
    label = _write_led_color(led, red, green, blue)
    logger.info(
        "[keyboard-rgb] zone %d color %d %d %d -> %s",
        zone, red, green, blue, label,
    )
    return label


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
