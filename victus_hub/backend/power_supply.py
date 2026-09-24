"""Read the laptop's AC state and battery charge from sysfs."""

from pathlib import Path

from victus_hub.backend.sysfs_read import read_int, read_text

_POWER_SUPPLY = Path("/sys/class/power_supply")


def power_status_text() -> str:
    """Return a short footer label, or a dash when power data is unavailable."""
    try:
        supplies = sorted(_POWER_SUPPLY.iterdir())
    except OSError:
        return "—"

    saw_mains = False
    mains_online = False
    battery = None
    for supply in supplies:
        kind = read_text(supply / "type")
        if kind in {"Mains", "ADP", "USB"}:
            saw_mains = True
            mains_online |= read_text(supply / "online") == "1"
        elif kind == "Battery" and battery is None:
            battery = supply

    if saw_mains:
        source = "AC" if mains_online else "Battery"
    elif battery is not None:
        status = (read_text(battery / "status") or "").lower()
        if status in {"charging", "full", "not charging"}:
            source = "AC"
        elif status == "discharging":
            source = "Battery"
        else:
            return "—"
    else:
        return "—"

    if battery is None:
        return source
    capacity = read_int(battery / "capacity")
    return f"{source} · {capacity}%" if capacity is not None and 0 <= capacity <= 100 else source
