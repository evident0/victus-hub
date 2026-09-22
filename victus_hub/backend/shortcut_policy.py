"""Qt-free policy for global shortcuts; ordinary typing is never exported."""

MODIFIERS = frozenset({29, 42, 54, 56, 97, 100, 125, 126, 464})
COMMAND_MODIFIERS = frozenset({29, 56, 97, 100, 125, 126})
SPECIAL_KEYS = frozenset({*range(59, 69), 87, 88, 138, 148, 149, 171, 202, 203, 226})


def validate_shortcut(mods, key) -> tuple[tuple[int, ...], int]:
    if not isinstance(mods, (list, tuple)) or any(type(m) is not int for m in mods):
        raise RuntimeError("invalid shortcut modifiers")
    if type(key) is not int or not 0 <= key <= 0x2ff or key in MODIFIERS:
        raise RuntimeError("invalid shortcut key")
    normalized = tuple(sorted(set(mods)))
    if not set(normalized) <= MODIFIERS:
        raise RuntimeError("invalid shortcut modifiers")
    if key and not (set(normalized) & COMMAND_MODIFIERS or key in SPECIAL_KEYS):
        raise RuntimeError("Use Ctrl, Alt or Super with a key, or a function/OMEN key")
    return normalized, key
