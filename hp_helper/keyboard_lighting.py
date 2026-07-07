"""Keyboard lighting state (static color + idle only)."""

import re
from dataclasses import dataclass

from PySide6.QtCore import QSettings


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
    color: str = "#35baf2"
    idle_timeout: int = 0  # seconds, 0 = disabled

DEFAULT_LIGHTING_SETTINGS = LightingSettings()

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_lighting_settings(settings: LightingSettings) -> LightingSettings:
    """Clamp and validate (no effects/speed anymore)."""
    return LightingSettings(
        enabled=settings.enabled,
        color=settings.color if _HEX_COLOR_RE.match(settings.color)
              else DEFAULT_LIGHTING_SETTINGS.color,
        idle_timeout=max(0, min(settings.idle_timeout, 3600)),
    )


# ── QSettings persistence ──

def read_lighting_settings() -> LightingSettings:
    s = QSettings()
    raw = s.value("keyboardLighting")
    if raw is None:
        return DEFAULT_LIGHTING_SETTINGS
    try:
        return LightingSettings(
            enabled=bool(raw.get("enabled", True)),
            color=raw.get("color", "#35baf2"),
            idle_timeout=int(raw.get("idle_timeout", 0)),
        )
    except Exception:
        return DEFAULT_LIGHTING_SETTINGS
def write_lighting_settings(settings: LightingSettings):
    s = QSettings()
    s.setValue("keyboardLighting", {
        "enabled": settings.enabled,
        "color": settings.color,
        "idle_timeout": settings.idle_timeout,
    })
