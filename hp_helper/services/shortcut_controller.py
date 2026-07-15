"""Polls the daemon for keyboard events and drives the program shortcut.

Owns a single QTimer that asks the daemon for the latest physical keypress
every ~100 ms.  Two modes:

* **Capture** — ``start_capture()`` arms the controller so the *next*
  non-modifier keypress is reported via ``captured(mods, key)`` instead of
  being matched against the configured keybind.  Used by the settings page
  to record a new shortcut.

* **Match** — when not capturing, each new keypress is compared with the
  persisted keybind; on a match ``triggered()`` fires (wired by the main
  window to restore/show the app).
"""

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from hp_helper import api
from hp_helper.features.keyboard.shortcut import (
    KeybindSettings,
    read_keybind_settings,
)

logger = logging.getLogger(__name__)


class ShortcutController(QObject):
    triggered = Signal()
    captured = Signal(object, int)  # mods frozenset, key code

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings: KeybindSettings = read_keybind_settings()
        self._last_seq: int = -1
        self._capturing: bool = False

        # Don't fire on a stale event from before the controller existed.
        try:
            self._last_seq = api.get_keyboard_last_event().seq
        except Exception:
            logger.debug("shortcut: daemon unreachable at init")

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── Public API ──

    def reload_settings(self) -> None:
        self._settings = read_keybind_settings()

    def start_capture(self) -> None:
        """Arm capture mode: the next non-modifier press becomes the bind."""
        self._capturing = True
        # Ignore presses that happened before the user clicked "Set".
        try:
            self._last_seq = api.get_keyboard_last_event().seq
        except Exception:
            pass

    def cancel_capture(self) -> None:
        self._capturing = False

    def is_capturing(self) -> bool:
        return self._capturing

    # ── Tick ──

    def _tick(self) -> None:
        try:
            ev = api.get_keyboard_last_event()
        except Exception:
            return
        if ev.seq == self._last_seq:
            return
        self._last_seq = ev.seq
        if ev.key == 0:
            return  # modifier-only change, nothing to capture or match

        if self._capturing:
            self._capturing = False
            self.captured.emit(frozenset(ev.mods), ev.key)
            return

        if self._settings.enabled and ev.key == self._settings.key \
           and frozenset(ev.mods) == frozenset(self._settings.mods):
            self.triggered.emit()
