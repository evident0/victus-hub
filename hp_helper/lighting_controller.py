"""Owns the keyboard lighting timer (static color + idle dim).

Emits `frame_changed` (RgbColor) for the preview visual.
"""

import time

from PySide6.QtCore import QObject, QTimer, Signal

from hp_helper import api
from hp_helper.keyboard_lighting import (
    LightingSettings,
    RgbColor,
    hex_to_rgb,
    normalize_lighting_settings,
    read_lighting_settings,
    write_lighting_settings,
)


class LightingController(QObject):
    frame_changed = Signal(object)  # RgbColor


    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = read_lighting_settings()
        self._last_send = 0.0
        self._last_sent_color: tuple[int, int, int] | None = None
        self._backlight_on: bool | None = None  # None = unknown hardware state
        self._last_idle_poll = 0.0
        self._dimmed = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def shutdown(self):
        """Stop the timer and turn the backlight off (called on app quit)."""
        self._timer.stop()
        try:
            api.set_keyboard_brightness(0)
            self._backlight_on = False
        except Exception:
            pass

    # ── Settings mutators (called from KeyboardPage signal handlers) ──

    def set_enabled(self, enabled: bool) -> None:
        self._update(enabled=enabled)

    def set_color(self, color: str) -> None:
        self._update(color=color)

    def set_idle_timeout(self, timeout: int) -> None:
        self._update(idle_timeout=max(0, timeout))
        # If idle timeout was disabled while dimmed, wake the backlight
        if timeout == 0 and self._dimmed:
            self._dimmed = False
            self._last_sent_color = None

    def _update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self._settings, key, value)
        write_lighting_settings(self._settings)

    # ── Animation tick ──

    def _tick(self) -> None:
        settings = normalize_lighting_settings(self._settings)
        now = time.monotonic()

        # ── Idle timeout polling (every 500 ms) ──
        if now - self._last_idle_poll >= 0.5:
            self._last_idle_poll = now
            if settings.idle_timeout > 0 and settings.enabled:
                try:
                    idle_elapsed = api.get_keyboard_idle_elapsed()
                except Exception:
                    idle_elapsed = 0.0
                if idle_elapsed < 0:
                    # Watcher thread not running — don't dim, ensure backlight stays on
                    if self._dimmed:
                        self._dimmed = False
                        self._last_sent_color = None
                        self._backlight_on = None
                elif idle_elapsed >= settings.idle_timeout and not self._dimmed:
                    self._dimmed = True
                    self._last_sent_color = None  # force re-evaluation below
                elif idle_elapsed < settings.idle_timeout and self._dimmed:
                    # User typed — restore backlight on next frame
                    self._dimmed = False
                    self._last_sent_color = None
                    self._backlight_on = None
            elif self._dimmed:
                self._dimmed = False
                self._last_sent_color = None
                self._backlight_on = None

        # Determine the desired hardware state for this frame.
        # "Off" is achieved via brightness=0 (the proper LED off path);
        # "On" is achieved by sending the color (which forces on at full).
        want_off = (not settings.enabled) or self._dimmed

        if want_off:
            if self._backlight_on is not False:
                if now - self._last_send >= 0.200:
                    try:
                        api.set_keyboard_brightness(0)
                    except Exception:
                        pass
                    # Mark off even on failure so a missing RGB module
                    # only logs once instead of spamming every tick.
                    self._backlight_on = False
                    self._last_send = now
                    self._last_sent_color = None
            self.frame_changed.emit(RgbColor(0, 0, 0))
            return

        # Enabled and not dimmed — ensure backlight is on with the chosen color.
        frame = hex_to_rgb(settings.color)
        color = (frame.red, frame.green, frame.blue)

        need_color_write = (self._backlight_on is not True) or (color != self._last_sent_color)
        if now - self._last_send >= 0.200 and need_color_write:
            try:
                api.set_keyboard_color(*color)
                self._last_send = now
                self._last_sent_color = color
                self._backlight_on = True
            except Exception:
                # Remember the attempted color so we only log once per distinct color.
                self._last_send = now
                self._last_sent_color = color
                self._backlight_on = True

        # Always update visual keyboard (responsive UI)
        self.frame_changed.emit(frame)

