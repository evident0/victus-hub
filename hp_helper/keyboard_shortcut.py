"""Program-shortcut keybind settings (open/unhide the app via a hotkey).

A keybind is a set of modifier keycodes plus one non-modifier key, expressed
in Linux input-event-codes (``linux/input-event-codes.h``).  The OMEN key on
HP laptops surfaces as a normal keycode on the "HP WMI hotkeys" input device
(e.g. KEY_PROG1 = 148), so it is represented as a single-key bind with no
modifiers.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QSettings


@dataclass
class KeyEvent:
    """A single non-modifier keypress reported by the daemon."""
    mods: tuple[int, ...]  # modifier keycodes held at press time (sorted)
    key: int               # non-modifier keycode, 0 if none recorded
    seq: int               # monotonic counter; bumps on each recorded press


# ── Linux input keycodes (subset of linux/input-event-codes.h) ──

KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_RIGHTCTRL = 97
KEY_RIGHTSHIFT = 54
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126

#: Keycodes treated as modifiers when matching a captured combo.
MODIFIERS: frozenset[int] = frozenset({
    KEY_LEFTCTRL, KEY_RIGHTCTRL,
    KEY_LEFTSHIFT, KEY_RIGHTSHIFT,
    KEY_LEFTALT, KEY_RIGHTALT,
    KEY_LEFTMETA, KEY_RIGHTMETA,
})


# ── Friendly names ──

_MOD_LABELS: dict[int, str] = {
    KEY_LEFTCTRL: "Ctrl", KEY_RIGHTCTRL: "Ctrl",
    KEY_LEFTSHIFT: "Shift", KEY_RIGHTSHIFT: "Shift",
    KEY_LEFTALT: "Alt", KEY_RIGHTALT: "Alt",
    KEY_LEFTMETA: "Super", KEY_RIGHTMETA: "Super",
}

# Order in which modifier labels are displayed.
_MOD_ORDER: list[str] = ["Ctrl", "Shift", "Alt", "Super"]

# Special, non-printable keycodes that benefit from a friendly name.
_SPECIAL_KEY_NAMES: dict[int, str] = {
    1: "Esc", 14: "Backspace", 15: "Tab", 28: "Enter", 57: "Space",
    58: "Caps", 102: "Home", 103: "Up", 104: "Page Up", 105: "Left",
    106: "Right", 107: "End", 108: "Down", 109: "Page Down",
    110: "Insert", 111: "Delete",
    # HP WMI hotkey candidates (OMEN key etc.)
    138: "Help", 148: "Omen Key", 149: "Prog2", 171: "Config",
    172: "Home Key", 202: "Prog3", 203: "Prog4", 226: "Media",
}


# Letter keycodes are QWERTY scan-order (linux/input-event-codes.h), not
# alphabetical, so they need an explicit map.
_LETTER_KEY_NAMES: dict[int, str] = {
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U",
    23: "I", 24: "O", 25: "P",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G", 35: "H", 36: "J",
    37: "K", 38: "L",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M",
}

def key_name(code: int) -> str:
    """Human-readable label for a single keycode."""
    if code in _SPECIAL_KEY_NAMES:
        return _SPECIAL_KEY_NAMES[code]
    if code in _LETTER_KEY_NAMES:
        return _LETTER_KEY_NAMES[code]
    if 2 <= code <= 10:  # KEY_1 .. KEY_9
        return str(code - 1)
    if code == 11:  # KEY_0
        return "0"
    if 59 <= code <= 68:  # KEY_F1 .. KEY_F10
        return f"F{code - 58}"
    if code == 87:  # KEY_F11
        return "F11"
    if code == 88:  # KEY_F12
        return "F12"
    return f"Key {code}"


def keybind_label(mods, key: int) -> str:
    """Render a keybind as ``"Ctrl + Shift + O"`` / ``"Omen Key"``.

    ``mods`` is any iterable of modifier keycodes.  Returns ``"Not set"``
    when no main key is configured.
    """
    if not key:
        return "Not set"
    seen: list[str] = []
    for code in mods:
        label = _MOD_LABELS.get(int(code))
        if label and label not in seen:
            seen.append(label)
    ordered = [m for m in _MOD_ORDER if m in seen]
    return " + ".join(ordered + [key_name(key)])


# ── Settings ──

@dataclass
class KeybindSettings:
    enabled: bool = True
    mods: tuple[int, ...] = field(default_factory=tuple)  # sorted ascending
    key: int = 0  # 0 = unset


DEFAULT_KEYBIND_SETTINGS = KeybindSettings()


def normalize_mods(mods) -> tuple[int, ...]:
    return tuple(sorted(int(m) for m in mods if int(m) in MODIFIERS))


def read_keybind_settings() -> KeybindSettings:
    s = QSettings()
    raw = s.value("programShortcut")
    if raw is None:
        return DEFAULT_KEYBIND_SETTINGS
    try:
        return KeybindSettings(
            enabled=bool(raw.get("enabled", True)),
            mods=normalize_mods(raw.get("mods", []) or []),
            key=int(raw.get("key", 0)),
        )
    except Exception:
        return DEFAULT_KEYBIND_SETTINGS


def write_keybind_settings(settings: KeybindSettings) -> None:
    s = QSettings()
    s.setValue("programShortcut", {
        "enabled": settings.enabled,
        "mods": list(settings.mods),
        "key": settings.key,
    })


def keybind_from_event(mods, key: int) -> KeybindSettings:
    """Build a KeybindSettings from a captured (mods, key) event."""
    return KeybindSettings(
        enabled=True,
        mods=normalize_mods(mods),
        key=int(key),
    )
