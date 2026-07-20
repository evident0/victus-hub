"""Keyboard lighting state (static color + idle only).

Supports single-zone (one color) and 4-zone keyboards. Zone order matches
the kernel module LED registration:

  0 = right, 1 = center, 2 = left, 3 = wasd
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from PySide6.QtCore import QSettings

# Kernel LED zone order for 4-zone keyboards.
ZONE_NAMES: tuple[str, ...] = ("Right", "Center", "Left", "WASD")
ZONE_COUNT_MULTI = 4
DEFAULT_COLOR = "#35baf2"

# Keys that belong to the dedicated WASD zone on 4-zone hardware.
_WASD_LABELS = frozenset({"w", "a", "s", "d"})


@dataclass
class RgbColor:
    red: int = 0
    green: int = 0
    blue: int = 0


def hex_to_rgb(hex_str: str) -> RgbColor:
    """Convert #rrggbb to RgbColor. Used for static color handling."""
    value = int(hex_str.lstrip("#"), 16)
    return RgbColor(
        red=(value >> 16) & 0xFF,
        green=(value >> 8) & 0xFF,
        blue=value & 0xFF,
    )


@dataclass
class LightingSettings:
    enabled: bool = True
    color: str = DEFAULT_COLOR
    # Per-zone hex colors for multi-zone hardware (length 4). When empty,
    # all zones fall back to ``color`` (single-zone and legacy settings).
    zone_colors: list[str] = field(default_factory=list)
    idle_timeout: int = 0  # seconds, 0 = disabled
    brightness: int = 255  # 0-255 backlight intensity


DEFAULT_LIGHTING_SETTINGS = LightingSettings()

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _valid_hex(color: str, fallback: str = DEFAULT_COLOR) -> str:
    return color if _HEX_COLOR_RE.match(color) else fallback


def normalize_zone_colors(
    color: str,
    zone_colors: list[str] | None,
    zone_count: int,
) -> list[str]:
    """Return a list of *zone_count* valid hex colors.

    Missing entries are filled from *color* (single-zone / legacy path).
    """
    primary = _valid_hex(color)
    if zone_count <= 1:
        return [primary]
    raw = list(zone_colors or [])
    out: list[str] = []
    for i in range(zone_count):
        if i < len(raw):
            out.append(_valid_hex(raw[i], primary))
        else:
            out.append(primary)
    return out


def normalize_lighting_settings(
    settings: LightingSettings,
    zone_count: int = 1,
) -> LightingSettings:
    """Clamp and validate (no effects/speed anymore)."""
    primary = _valid_hex(settings.color)
    zones = normalize_zone_colors(primary, settings.zone_colors, max(1, zone_count))
    return LightingSettings(
        enabled=settings.enabled,
        color=primary if zone_count <= 1 else zones[0],
        zone_colors=zones if zone_count > 1 else [],
        idle_timeout=max(0, min(settings.idle_timeout, 3600)),
        brightness=max(0, min(settings.brightness, 255)),
    )


def zone_for_key(label: str, key_center_x: float, row_width: float = 15.0) -> int:
    """Map a preview key to a hardware zone index (4-zone layout).

    WASD keys always use zone 3. Other keys are split into left / center /
    right thirds of the main row width (zones 2 / 1 / 0).
    """
    if label.lower() in _WASD_LABELS:
        return 3  # wasd
    if row_width <= 0:
        return 1
    third = row_width / 3.0
    if key_center_x < third:
        return 2  # left
    if key_center_x < 2.0 * third:
        return 1  # center
    return 0  # right


# ── QSettings persistence ──

def read_lighting_settings() -> LightingSettings:
    s = QSettings()
    raw = s.value("keyboardLighting")
    if raw is None:
        return DEFAULT_LIGHTING_SETTINGS
    try:
        primary = raw.get("color", DEFAULT_COLOR)
        zone_raw = raw.get("zone_colors") or []
        zone_colors: list[str] = []
        if isinstance(zone_raw, (list, tuple)):
            zone_colors = [str(c) for c in zone_raw]
        return LightingSettings(
            enabled=bool(raw.get("enabled", True)),
            color=str(primary),
            zone_colors=zone_colors,
            idle_timeout=int(raw.get("idle_timeout", 0)),
            brightness=int(raw.get("brightness", 255)),
        )
    except Exception:
        return DEFAULT_LIGHTING_SETTINGS


def write_lighting_settings(settings: LightingSettings):
    s = QSettings()
    payload = {
        "enabled": settings.enabled,
        "color": settings.color,
        "idle_timeout": settings.idle_timeout,
        "brightness": settings.brightness,
    }
    if settings.zone_colors:
        payload["zone_colors"] = list(settings.zone_colors)
    s.setValue("keyboardLighting", payload)
