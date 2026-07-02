"""Owns the keyboard-lighting animation timer and idle-dim state.

Extracted from MainWindow. Knows nothing about KeyboardPage; emits `frame_changed`
(RgbColor) signal that the window connects to the page.
"""

import time

from PySide6.QtCore import QObject, QTimer, Signal

from hp_helper import api
from hp_helper.keyboard_lighting import (
    LightingSettings,
    RgbColor,
    lighting_frame,
    normalize_lighting_settings,
    read_lighting_settings,
    write_lighting_settings,
)


class LightingController(QObject):
    frame_changed = Signal(object)  # RgbColor


    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = read_lighting_settings()
        self._started_at_ms = int(time.time() * 1000)
        self._last_send = 0.0
        self._last_sent_color: tuple[int, int, int] | None = None
        self._last_idle_poll = 0.0
        self._dimmed = False

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def shutdown(self):
        self._timer.stop()

    # ── Settings mutators (called from KeyboardPage signal handlers) ──

    def set_enabled(self, enabled: bool) -> None:
        self._update(enabled=enabled)

    def set_effect(self, effect: str) -> None:
        self._update(effect=effect)

    def set_color(self, color: str) -> None:
        self._update(color=color)

    def set_speed(self, speed: int) -> None:
        self._update(speed=speed)

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
                elif idle_elapsed >= settings.idle_timeout and not self._dimmed:
                    self._dimmed = True
                    try:
                        api.set_keyboard_color(0, 0, 0)
                    except Exception:
                        pass
                    self._last_sent_color = (0, 0, 0)
                elif idle_elapsed < settings.idle_timeout and self._dimmed:
                    # User typed — restore backlight on next frame
                    self._dimmed = False
                    self._last_sent_color = None
            elif self._dimmed:
                self._dimmed = False
                self._last_sent_color = None

        # If dimmed by idle timeout, emit black frame and skip normal output
        if self._dimmed:
            if self._last_sent_color == (0, 0, 0):
                self.frame_changed.emit(RgbColor(0, 0, 0))
            return

        elapsed = int(time.time() * 1000 - self._started_at_ms)
        frame = lighting_frame(settings, elapsed)

        # Throttle daemon call: only send every 200 ms and only on color change
        color = (frame.red, frame.green, frame.blue)
        if now - self._last_send >= 0.200 and color != self._last_sent_color:
            try:
                api.set_keyboard_color(*color)
                self._last_send = now
                self._last_sent_color = color
            except Exception:
                pass

        # Always update visual keyboard (responsive UI)
        self.frame_changed.emit(frame)

