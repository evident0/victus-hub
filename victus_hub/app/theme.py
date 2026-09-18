"""Ohman palette, IBM Plex fonts, and the mode accent every control shares."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

# Neutral dark panel. Names match the Ohman XAML resources; older keys stay as
# aliases so existing widgets keep working after the restyle.
ECO = "#2FBF8F"
BALANCED = "#3F8CFF"
PERF = "#E2572C"
WARN = "#F3821D"
DANGER = "#FF5C5C"
OK = "#4AC06C"

MODE_NAMES = ("Eco", "Balanced", "Performance")

COLORS = {
    "bg": "#161616",
    "card": "#161616",
    "sunken": "#0F0F0F",
    "well": "#0D0D0D",
    "pill": "#202020",
    "line": "#2C2C2C",
    "line2": "#272727",
    "edge": "#343434",
    "track": "#313131",
    "switch_off": "#363636",
    "knob_off": "#8A8A8A",
    "text": "#EDEDED",
    "text_hi": "#F4F4F4",
    "seg_text": "#A8A8A8",
    "hex": "#A2A2A2",
    "sub": "#969696",
    "desc": "#909090",
    "status": "#8A8A8A",
    "axis": "#848484",
    "foot": "#7E7E7E",
    "section": "#787878",
    "accent": BALANCED,
    "accent_hi": "#6BA6FF",
    "ok": OK,
    "warn": WARN,
    "danger": DANGER,
    "eco": ECO,
    "balanced": BALANCED,
    "perf": PERF,
    # aliases used by older widgets
    "surface": "#161616",
    "surface_raised": "#202020",
    "text_secondary": "#969696",
    "accent_green": ECO,
    "accent_red": DANGER,
    "border": "#2C2C2C",
    "border_focus": "#343434",
}

UI_FONT = "IBM Plex Sans"
MONO_FONT = "IBM Plex Mono"
_FONTS_LOADED = False


def _fonts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "fonts"


def load_fonts() -> None:
    """Register the bundled IBM Plex files. Falls back to system UI fonts."""
    global UI_FONT, MONO_FONT, _FONTS_LOADED
    if _FONTS_LOADED:
        return
    names = (
        "IBMPlexSans-Regular.ttf",
        "IBMPlexSans-Medium.ttf",
        "IBMPlexSans-SemiBold.ttf",
        "IBMPlexMono-Regular.ttf",
        "IBMPlexMono-Medium.ttf",
    )
    loaded = 0
    fonts = _fonts_dir()
    for name in names:
        path = fonts / name
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) != -1:
            loaded += 1
    if loaded:
        UI_FONT = "IBM Plex Sans"
        MONO_FONT = "IBM Plex Mono"
    else:
        UI_FONT = "Segoe UI"
        MONO_FONT = "Consolas"
    _FONTS_LOADED = True


def ui_font(pixel: int = 14, weight: int = QFont.Weight.Normal) -> QFont:
    font = QFont(UI_FONT)
    font.setPixelSize(pixel)
    font.setWeight(QFont.Weight(weight))
    return font


def mono_font(pixel: int = 11, weight: int = QFont.Weight.Normal) -> QFont:
    font = QFont(MONO_FONT)
    font.setPixelSize(pixel)
    font.setWeight(QFont.Weight(weight))
    return font


def mode_color(index: int) -> str:
    if index <= 0:
        return ECO
    if index >= 2:
        return PERF
    return BALANCED


def mode_name(index: int) -> str:
    if index <= 0:
        return MODE_NAMES[0]
    if index >= 2:
        return MODE_NAMES[2]
    return MODE_NAMES[1]


def mix_hex(hex_color: str, towards: str, t: float) -> str:
    c = QColor(hex_color)
    o = QColor(towards)
    return QColor(
        int(c.red() + (o.red() - c.red()) * t),
        int(c.green() + (o.green() - c.green()) * t),
        int(c.blue() + (o.blue() - c.blue()) * t),
    ).name()


def set_accent(index: int) -> str:
    """Point the shared accent at this performance mode. Returns the hex."""
    color = mode_color(index)
    COLORS["accent"] = color
    COLORS["accent_hi"] = mix_hex(color, "#FFFFFF", 0.22)
    return color


def hsl(h: float, s: float, l: float) -> QColor:
    """Ohman's HSL helper: h in degrees, s/l in 0..1."""
    h = ((h % 360) + 360) % 360
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0.0
    elif h < 120:
        r, g, b = x, c, 0.0
    elif h < 180:
        r, g, b = 0.0, c, x
    elif h < 240:
        r, g, b = 0.0, x, c
    elif h < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return QColor(
        round((r + m) * 255),
        round((g + m) * 255),
        round((b + m) * 255),
    )


def stylesheet() -> str:
    """Load style.qss and substitute @token colour names."""
    path = Path(__file__).resolve().parent.parent / "resources" / "style.qss"
    text = path.read_text(encoding="utf-8")
    tokens = dict(COLORS)
    tokens["ui_font"] = UI_FONT
    tokens["mono_font"] = MONO_FONT
    icons = path.parent / "icons"
    tokens["chevron_up"] = str(icons / "chevron-up.png")
    tokens["chevron_down"] = str(icons / "chevron-down.png")
    # longer keys first so @accent_hi is not eaten by @accent
    for key in sorted(tokens, key=len, reverse=True):
        text = text.replace("@" + key, str(tokens[key]))
    return text
