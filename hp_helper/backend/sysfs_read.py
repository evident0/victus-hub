"""Shared sysfs read helpers for the unprivileged app and the root daemon.

The daemon's own comment said its hwmon-discovery was "duplicated from
backend/sensors.py". This module is the single source of truth.
"""

from pathlib import Path

_HWMON_ROOT = Path("/sys/class/hwmon")


def iter_hwmon_dirs() -> list[Path]:
    """Return the list of /sys/class/hwmon/hwmon* entries, sorted by name.

    Returns an empty list on any OSError (e.g. the path doesn't exist
    in a sandbox or the user lacks read permission).
    """
    try:
        return sorted(
            entry for entry in _HWMON_ROOT.iterdir() if entry.name.startswith("hwmon")
        )
    except OSError:
        return []


def read_text(path: Path) -> str | None:
    """Read a sysfs path and return the stripped text, or None on OSError."""
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_int(path: Path) -> int | None:
    """Read a sysfs path and parse it as int, or None on any error."""
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def find_hwmon_by_name(*names: str) -> Path | None:
    """Return the first hwmon directory whose `name` file matches one of `names`.

    Names are matched case-insensitively against the lowercased contents of
    each hwmon's `name` file (falling back to the directory basename when
    the `name` file is unreadable).
    """
    wanted = {n.lower() for n in names}
    for hwmon in iter_hwmon_dirs():
        name = read_text(hwmon / "name")
        if name is None:
            name = hwmon.name
        if name.lower() in wanted:
            return hwmon
    return None


def read_hp_pwm_pct() -> float | None:
    """Read the HP hwmon pwm1 value and return it as a percentage (0-100).

    Returns None when the hp hwmon or pwm1 node is unavailable. Used by
    the fan-control loop to seed its state on resume from a preset.
    """
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    if hwmon is None:
        return None
    pwm = read_int(hwmon / "pwm1")
    if pwm is None:
        return None
    return pwm / 255.0 * 100.0
