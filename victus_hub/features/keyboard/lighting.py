"""Keyboard lighting state: static color plus software RGB effects.

Effects are computed in userspace — the kernel module only accepts static
per-zone colors. Animation math matches omen-space's ``compute_anim_color``
(``src/omen-space-daemon/src/rgb.rs``).

Zone order matches the kernel module LED registration:

  0 = right, 1 = center, 2 = left, 3 = wasd
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from PySide6.QtCore import QSettings

# Kernel LED zone order for 4-zone keyboards.
ZONE_NAMES: tuple[str, ...] = ("Right", "Center", "Left", "WASD")
ZONE_COUNT_MULTI = 4
DEFAULT_COLOR = "#35baf2"
DEFAULT_COLOR2 = "#0000ff"
DEFAULT_SPEED = 50

# Left-to-right spatial order for 4-zone hardware so wave/chase travel
# across the board instead of following LED registration order.
# zone 2 (left) → 0, zone 3 (wasd) → 1, zone 1 (center) → 2, zone 0 (right) → 3
_ZONE_SPATIAL_LTR: tuple[int, ...] = (3, 2, 0, 1)

# Keys that belong to the dedicated WASD zone on 4-zone hardware.
_WASD_LABELS = frozenset({"w", "a", "s", "d"})

# omen-space GUI lists, plus the extra zone-based software modes from
# VALID_MODES in rgb.rs (chase/sparkle/candle/aurora/disco/gradient).
LIGHTING_EFFECTS_SINGLE: tuple[tuple[str, str], ...] = (
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("cycle", "Color Cycle"),
)
LIGHTING_EFFECTS_MULTI: tuple[tuple[str, str], ...] = (
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("blinking", "Blinking"),
    ("cycle", "Color Cycle"),
    ("wave", "Wave"),
    ("wave_rainbow", "Wave Rainbow"),
    ("chase", "Chase"),
    ("sparkle", "Sparkle"),
    ("candle", "Candle"),
    ("aurora", "Aurora"),
    ("disco", "Disco"),
    ("gradient", "Gradient"),
)

_ALL_EFFECTS: frozenset[str] = frozenset(
    value
    for value, _label in (*LIGHTING_EFFECTS_SINGLE, *LIGHTING_EFFECTS_MULTI)
)
# Aliases accepted from persisted settings / omen-space names.
_EFFECT_ALIASES: dict[str, str] = {
    "pulse": "breathing",
    "rainbow": "wave_rainbow",
    "color-cycle": "cycle",
    "strobe": "blinking",
}

# Effects that interpolate color + color2.
EFFECTS_NEED_COLOR2: frozenset[str] = frozenset({"wave", "gradient"})
# Effects that generate their own palette (color pickers unused).
EFFECTS_IGNORE_COLOR: frozenset[str] = frozenset(
    {"cycle", "wave_rainbow", "aurora", "disco"}
)

STATIC_INTERVAL_MS = 200
ANIM_INTERVAL_MS = 50


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
    effect: str = "static"
    color: str = DEFAULT_COLOR
    color2: str = DEFAULT_COLOR2
    speed: int = DEFAULT_SPEED
    # Per-zone hex colors for multi-zone hardware (length 4). When empty,
    # all zones fall back to ``color`` (single-zone and legacy settings).
    zone_colors: list[str] = field(default_factory=list)
    idle_timeout: int = 0  # seconds, 0 = disabled
    brightness: int = 255  # 0-255 backlight intensity


DEFAULT_LIGHTING_SETTINGS = LightingSettings()

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _valid_hex(color: str, fallback: str = DEFAULT_COLOR) -> str:
    return color if _HEX_COLOR_RE.match(color) else fallback


def effects_for_zone_count(zone_count: int) -> tuple[tuple[str, str], ...]:
    """Return the effect list shown in the keyboard panel."""
    if zone_count <= 1:
        return LIGHTING_EFFECTS_SINGLE
    return LIGHTING_EFFECTS_MULTI


def normalize_effect(effect: str, zone_count: int = 1) -> str:
    raw = (effect or "static").strip().lower()
    raw = _EFFECT_ALIASES.get(raw, raw)
    allowed = {value for value, _label in effects_for_zone_count(zone_count)}
    if raw in allowed:
        return raw
    if raw in _ALL_EFFECTS:
        # Persisted multi-zone effect on single-zone hardware (or vice versa).
        return "static"
    return "static"


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
    """Clamp and validate effect, colors, speed, idle, and brightness."""
    primary = _valid_hex(settings.color)
    zones = normalize_zone_colors(primary, settings.zone_colors, max(1, zone_count))
    return LightingSettings(
        enabled=settings.enabled,
        effect=normalize_effect(settings.effect, zone_count),
        color=primary if zone_count <= 1 else zones[0],
        color2=_valid_hex(settings.color2, DEFAULT_COLOR2),
        speed=max(1, min(int(settings.speed), 100)),
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


def spatial_index(zone: int, zone_count: int) -> int:
    """Map a hardware zone index onto left-to-right animation order."""
    if zone_count <= 1:
        return 0
    if 0 <= zone < len(_ZONE_SPATIAL_LTR):
        return _ZONE_SPATIAL_LTR[zone]
    return zone


def effect_is_animated(effect: str) -> bool:
    return normalize_effect(effect, zone_count=4) != "static"


def _u8(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value)


def _fnv_mix(eff_idx: int, step_i: int) -> int:
    """Stable 64-bit mix (Python's hash() is randomized per process)."""
    h = 14695981039346656037
    for value in (eff_idx, step_i):
        h ^= value & 0xFFFFFFFFFFFFFFFF
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def compute_anim_color(
    mode: str,
    step: float,
    eff_idx: int,
    zone_count: int,
    r1: int,
    g1: int,
    b1: int,
    r2: int,
    g2: int,
    b2: int,
) -> RgbColor:
    """Per-zone animation color — ports omen-space ``compute_anim_color``.

    ``cycle`` keeps every zone in phase (GUI color-cycle). ``wave_rainbow``
    (and the ``rainbow`` alias) adds a spatial hue offset so the rainbow
    travels across zones.
    """
    n = max(1, zone_count)
    two_pi = 2.0 * math.pi

    if mode == "wave":
        phase = step + (eff_idx * (two_pi / n))
        factor = (math.sin(phase) * 0.5) + 0.5
        inv = 1.0 - factor
        return RgbColor(
            red=_u8(r1 * factor + r2 * inv),
            green=_u8(g1 * factor + g2 * inv),
            blue=_u8(b1 * factor + b2 * inv),
        )
    if mode in ("rainbow", "wave_rainbow"):
        hue = step + (eff_idx * (two_pi / n))
        return RgbColor(
            red=_u8((math.sin(hue) * 127.0) + 128.0),
            green=_u8((math.sin(hue + two_pi / 3.0) * 127.0) + 128.0),
            blue=_u8((math.sin(hue + 4.0 * two_pi / 3.0) * 127.0) + 128.0),
        )
    if mode == "cycle":
        hue = step
        return RgbColor(
            red=_u8((math.sin(hue) * 127.0) + 128.0),
            green=_u8((math.sin(hue + two_pi / 3.0) * 127.0) + 128.0),
            blue=_u8((math.sin(hue + 4.0 * two_pi / 3.0) * 127.0) + 128.0),
        )
    if mode in ("breathing", "pulse"):
        factor = (math.sin(step) * 0.5) + 0.5
        return RgbColor(
            red=_u8(r1 * factor),
            green=_u8(g1 * factor),
            blue=_u8(b1 * factor),
        )
    if mode == "blinking":
        # Square wave: on for [0, π), off for [π, 2π). Compare against the
        # wrapped phase so float error in sin(π) cannot stick the LED on.
        phase = step % (2.0 * math.pi)
        factor = 1.0 if phase < math.pi else 0.0
        return RgbColor(
            red=_u8(r1 * factor),
            green=_u8(g1 * factor),
            blue=_u8(b1 * factor),
        )
    if mode == "chase":
        pos = int(step * 2.0) % n
        factor = 1.0 if eff_idx == pos else 0.15
        return RgbColor(
            red=_u8(r1 * factor),
            green=_u8(g1 * factor),
            blue=_u8(b1 * factor),
        )
    if mode == "sparkle":
        val = _fnv_mix(eff_idx, int(step))
        if val % 4 == 0:
            factor = 0.1 + (val % 90) / 100.0
        else:
            factor = 0.2
        return RgbColor(
            red=_u8(r1 * factor),
            green=_u8(g1 * factor),
            blue=_u8(b1 * factor),
        )
    if mode == "candle":
        noise = (math.sin(step) * 0.3) + (math.sin(step * 2.3) * 0.15)
        factor = min(1.0, max(0.3, 0.6 + noise))
        return RgbColor(
            red=_u8(r1 * factor),
            green=_u8(g1 * 0.6 * factor),
            blue=_u8(b1 * 0.2 * factor),
        )
    if mode == "aurora":
        hs = (step * 0.3) + (eff_idx * 0.5)
        return RgbColor(
            red=_u8((math.sin(hs) * 40.0) + 40.0),
            green=_u8((math.cos(hs + 1.0) * 100.0) + 120.0),
            blue=_u8((math.sin(hs + 2.0) * 90.0) + 140.0),
        )
    if mode == "disco":
        beat = int(step * 1.5)
        seed = (beat + eff_idx) & 0xFFFFFFFFFFFFFFFF
        lcg = (
            seed * 6364136223846793005 + 1442695040888963407
        ) & 0xFFFFFFFFFFFFFFFF
        return RgbColor(
            red=(lcg >> 56) & 0xFF,
            green=(lcg >> 48) & 0xFF,
            blue=(lcg >> 40) & 0xFF,
        )
    if mode == "gradient":
        blend = (math.sin(step + eff_idx * 0.7) * 0.5) + 0.5
        inv = 1.0 - blend
        return RgbColor(
            red=_u8(r1 * inv + r2 * blend),
            green=_u8(g1 * inv + g2 * blend),
            blue=_u8(b1 * inv + b2 * blend),
        )
    return RgbColor(red=r1, green=g1, blue=b1)


_PER_ZONE_MODES: frozenset[str] = frozenset(
    {"breathing", "pulse", "blinking", "chase", "sparkle", "candle"}
)


def lighting_frames(
    settings: LightingSettings,
    zone_count: int,
    step: float,
) -> list[RgbColor]:
    """Compute one RGB color per hardware zone for the current animation step.

    Speed is applied by the caller (``anim_step`` increment). A 50 ms tick
    with ``step_inc = (speed / 100) * 0.25`` matches omen-space's loop.
    """
    n = max(1, zone_count)
    normalized = normalize_lighting_settings(settings, n)
    hexes = normalize_zone_colors(normalized.color, normalized.zone_colors, n)
    if not normalized.enabled:
        return [RgbColor(0, 0, 0) for _ in range(n)]
    if normalized.effect == "static":
        return [hex_to_rgb(h) for h in hexes]

    primary = hex_to_rgb(hexes[0])
    secondary = hex_to_rgb(normalized.color2)
    frames: list[RgbColor] = []
    for zone in range(n):
        spatial = spatial_index(zone, n)
        if normalized.effect in _PER_ZONE_MODES:
            source = hex_to_rgb(hexes[zone])
        else:
            source = primary
        frames.append(
            compute_anim_color(
                normalized.effect,
                step,
                spatial,
                n,
                source.red,
                source.green,
                source.blue,
                secondary.red,
                secondary.green,
                secondary.blue,
            )
        )
    return frames


def step_increment(speed: int, dt: float) -> float:
    """Advance ``anim_step`` for *dt* seconds at omen-space's 20 Hz rate.

    omen-space adds ``(speed / 100) * 0.25`` every 50 ms, i.e. ``speed / 20``
    per second.
    """
    clamped = max(1, min(int(speed), 100))
    return dt * (clamped / 20.0)


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
            effect=str(raw.get("effect", "static")),
            color=str(primary),
            color2=str(raw.get("color2", DEFAULT_COLOR2)),
            speed=int(raw.get("speed", DEFAULT_SPEED)),
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
        "effect": settings.effect,
        "color": settings.color,
        "color2": settings.color2,
        "speed": settings.speed,
        "idle_timeout": settings.idle_timeout,
        "brightness": settings.brightness,
    }
    if settings.zone_colors:
        payload["zone_colors"] = list(settings.zone_colors)
    s.setValue("keyboardLighting", payload)
