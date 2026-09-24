"""Hardware module / driver presence detection.

Status helpers return ``(text, color)`` for the Settings page.
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

    - Green ``"hp-wmi"`` — both mode and manual PWM controls exposed
    - Orange ``"limited"`` — hwmon present without manual PWM
    - Red ``"not supported"`` — no hp-wmi hwmon at all
    """
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    if hwmon is None:
        return ("not supported", RED)
    if (hwmon / "pwm1_enable").exists() and (hwmon / "pwm1").exists():
        return ("hp-wmi", GREEN)
    return ("limited", ORANGE)


def mux_module() -> tuple[str, str]:
    """Detect working GPU MUX support in hp-wmi."""
    from victus_hub.features.gpu.mux import read_gpu_mux_state

    if read_gpu_mux_state() is not None:
        return ("hp-wmi", GREEN)
    return ("not supported", RED)


def emulated_keyboard_zone_count() -> int | None:
    """Forced zone count from ``VICTUS_HUB_EMULATE_ZONES``, or None.

    Used by ``./scripts/ui-test`` so the panel can exercise 4-zone RGB
    without the kernel module or daemon.
    """
    raw = os.environ.get("VICTUS_HUB_EMULATE_ZONES", "").strip()
    if not raw:
        return None
    try:
        count = int(raw)
    except ValueError:
        return None
    return count if count >= 1 else None


def keyboard_rgb_module() -> tuple[str, str]:
    """Detect usable hp-kbd-rgb lighting controls.

    - Green ``"hp-kbd-rgb"`` if an RGB LED control exists
    - Green ``"emulated"`` if ``VICTUS_HUB_EMULATE_ZONES`` is set
    - Red ``"not supported"`` otherwise
    """
    if emulated_keyboard_zone_count() is not None:
        return ("emulated", GREEN)
    from victus_hub.backend.hardware_capabilities import keyboard_lighting_supported

    if keyboard_lighting_supported():
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
