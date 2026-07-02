"""Keyboard lighting state — ports keyboard-lighting.ts."""

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import QSettings

LightingEffect = Literal["static", "breathing", "color-cycle", "strobe"]


@dataclass
class RgbColor:
    red: int = 0
    green: int = 0
    blue: int = 0


@dataclass
class LightingSettings:
    enabled: bool = True
    effect: LightingEffect = "static"
    color: str = "#35baf2"
    speed: int = 50
    idle_timeout: int = 0  # seconds, 0 = disabled

LIGHTING_EFFECTS: list[dict] = [
    {"value": "static", "label": "Static"},
    {"value": "breathing", "label": "Breathing"},
    {"value": "color-cycle", "label": "Color Cycle"},
    {"value": "strobe", "label": "Strobe"},
]

DEFAULT_LIGHTING_SETTINGS = LightingSettings()

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
STATIC_INTERVAL_MS = 1000


def normalize_lighting_settings(settings: LightingSettings) -> LightingSettings:
    valid_effects = {e["value"] for e in LIGHTING_EFFECTS}
    return LightingSettings(
        enabled=settings.enabled,
        effect=settings.effect if settings.effect in valid_effects
               else DEFAULT_LIGHTING_SETTINGS.effect,
        color=settings.color if _HEX_COLOR_RE.match(settings.color)
              else DEFAULT_LIGHTING_SETTINGS.color,
        speed=min(100, max(1, round(settings.speed))),
        idle_timeout=max(0, min(settings.idle_timeout, 3600)),
    )


def hex_to_rgb(hex_str: str) -> RgbColor:
    value = int(hex_str.lstrip("#"), 16)
    return RgbColor(
        red=(value >> 16) & 0xFF,
        green=(value >> 8) & 0xFF,
        blue=value & 0xFF,
    )


def scale_color(color: RgbColor, scale: float) -> RgbColor:
    return RgbColor(
        red=round(color.red * scale),
        green=round(color.green * scale),
        blue=round(color.blue * scale),
    )


def hsv_to_rgb(hue: float) -> RgbColor:
    """Convert hue 0-1 to RGB (full saturation and value)."""
    sector = int(hue * 6)
    fraction = hue * 6 - sector
    q = 1 - fraction
    t = fraction

    sector %= 6
    if sector == 0:
        return RgbColor(red=255, green=round(t * 255), blue=0)
    elif sector == 1:
        return RgbColor(red=round(q * 255), green=255, blue=0)
    elif sector == 2:
        return RgbColor(red=0, green=255, blue=round(t * 255))
    elif sector == 3:
        return RgbColor(red=0, green=round(q * 255), blue=255)
    elif sector == 4:
        return RgbColor(red=round(t * 255), green=0, blue=255)
    else:
        return RgbColor(red=255, green=0, blue=round(q * 255))


def cycle_ms(speed: int) -> int:
    return 4500 - speed * 35


def lighting_frame(settings: LightingSettings, elapsed_ms: int) -> RgbColor:
    """Compute the keyboard color for the current frame."""
    normalized = normalize_lighting_settings(settings)
    if not normalized.enabled:
        return RgbColor(0, 0, 0)

    base = hex_to_rgb(normalized.color)
    if normalized.effect == "static":
        return base

    period = cycle_ms(normalized.speed)
    phase = (elapsed_ms % period) / period

    if normalized.effect == "color-cycle":
        return hsv_to_rgb(phase)

    if normalized.effect == "strobe":
        return base if phase < 0.5 else RgbColor(0, 0, 0)

    # breathing
    intensity = 0.5 - math.cos(phase * math.pi * 2) / 2.0
    return scale_color(base, intensity)


def frame_interval_ms(settings: LightingSettings) -> int:
    """Return the frame interval in ms for the current effect."""
    if not settings.enabled or settings.effect == "static":
        return STATIC_INTERVAL_MS
    return max(MIN_FRAME_INTERVAL_MS, round(cycle_ms(settings.speed) / 60))


# ── QSettings persistence ──

def read_lighting_settings() -> LightingSettings:
    s = QSettings()
    raw = s.value("keyboardLighting")
    if raw is None:
        return DEFAULT_LIGHTING_SETTINGS
    try:
        return LightingSettings(
            enabled=bool(raw.get("enabled", True)),
            effect=raw.get("effect", "static"),
            color=raw.get("color", "#35baf2"),
            speed=int(raw.get("speed", 50)),
            idle_timeout=int(raw.get("idle_timeout", 0)),
        )
    except Exception:
        return DEFAULT_LIGHTING_SETTINGS


def write_lighting_settings(settings: LightingSettings):
    s = QSettings()
    s.setValue("keyboardLighting", {
        "enabled": settings.enabled,
        "effect": settings.effect,
        "color": settings.color,
        "speed": settings.speed,
        "idle_timeout": settings.idle_timeout,
    })
