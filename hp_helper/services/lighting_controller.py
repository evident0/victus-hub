"""Owns the keyboard lighting timer (static color + idle dim).

Emits ``frame_changed`` with a list of ``RgbColor`` (length = zone count)
for the preview visual. Single-zone keyboards keep the original one-color
write path so behavior stays identical.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from hp_helper import api
from hp_helper.features.keyboard.lighting import (
    LightingSettings,
    RgbColor,
    hex_to_rgb,
    normalize_lighting_settings,
    normalize_zone_colors,
    read_lighting_settings,
    write_lighting_settings,
)


class LightingController(QObject):
    # list[RgbColor] — one entry per hardware zone
    frame_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zone_count = api.get_keyboard_zone_count()
        self._settings = read_lighting_settings()
        self._last_send = 0.0
        # Single-zone: one RGB tuple. Multi-zone: one per zone (or None).
        self._last_sent_color: tuple[int, int, int] | None = None
        self._last_sent_zone_colors: list[tuple[int, int, int] | None] = (
            [None] * self._zone_count
        )
        self._backlight_on: bool | None = None  # None = unknown hardware state
        self._last_idle_poll = 0.0
        self._dimmed = False
        self._last_sent_brightness: int | None = None
        # Sync the user-preferred brightness to the daemon before any color
        # write so _write_led_color applies it atomically (no 100% flash).
        try:
            api.set_keyboard_user_brightness(self._settings.brightness)
            self._last_sent_brightness = self._settings.brightness
        except Exception:
            pass
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def zone_count(self) -> int:
        return self._zone_count

    def shutdown(self):
        """Stop the timer and turn the backlight off (called on app quit)."""
        self._timer.stop()
        try:
            api.set_keyboard_brightness(0)
            self._backlight_on = False
        except Exception:
            pass

    def pause(self) -> None:
        """Stop the lighting timer (called on system suspend); unlike
        ``shutdown()``, does not turn the backlight off."""
        self._timer.stop()

    def resume(self) -> None:
        """Restart the lighting timer after suspend and force the next tick
        to re-evaluate/re-send the configured color."""
        self._backlight_on = None
        self._last_sent_color = None
        self._last_sent_zone_colors = [None] * self._zone_count
        self._timer.start()

    # ── Settings mutators (called from KeyboardPage signal handlers) ──

    def set_enabled(self, enabled: bool) -> None:
        self._update(enabled=enabled)

    def set_color(self, color: str) -> None:
        """Set the single global color (single-zone path)."""
        self._update(color=color, zone_colors=[])

    def set_zone_color(self, zone: int, color: str) -> None:
        """Update one zone's color on multi-zone keyboards."""
        zones = normalize_zone_colors(
            self._settings.color,
            self._settings.zone_colors,
            max(self._zone_count, 4),
        )
        if 0 <= zone < len(zones):
            zones[zone] = color
        # Keep legacy ``color`` in sync with zone 0 for older settings readers.
        primary = zones[0] if zones else color
        self._update(color=primary, zone_colors=zones)

    def set_idle_timeout(self, timeout: int) -> None:
        self._update(idle_timeout=max(0, timeout))
        # If idle timeout was disabled while dimmed, wake the backlight
        if timeout == 0 and self._dimmed:
            self._dimmed = False
            self._invalidate_sent()

    def set_brightness(self, level: int) -> None:
        """Set the user-preferred backlight brightness (0-255).

        Sends the level to the daemon immediately so that subsequent color
        writes apply it atomically (no 100% flash on enable / idle wake).
        """
        level = max(0, min(255, level))
        self._update(brightness=level)
        try:
            api.set_keyboard_user_brightness(level)
            self._last_sent_brightness = level
        except Exception:
            self._last_sent_brightness = None

    def _update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self._settings, key, value)
        write_lighting_settings(self._settings)

    def _invalidate_sent(self) -> None:
        self._last_sent_color = None
        self._last_sent_zone_colors = [None] * self._zone_count

    def _zone_rgb_list(self, settings: LightingSettings) -> list[RgbColor]:
        hexes = normalize_zone_colors(
            settings.color, settings.zone_colors, self._zone_count,
        )
        return [hex_to_rgb(h) for h in hexes]

    # ── Animation tick ──

    def _tick(self) -> None:
        settings = normalize_lighting_settings(self._settings, self._zone_count)
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
                        self._invalidate_sent()
                        self._backlight_on = None
                elif idle_elapsed >= settings.idle_timeout and not self._dimmed:
                    self._dimmed = True
                    self._invalidate_sent()  # force re-evaluation below
                elif idle_elapsed < settings.idle_timeout and self._dimmed:
                    # User typed — restore backlight on next frame
                    self._dimmed = False
                    self._invalidate_sent()
                    self._backlight_on = None
            elif self._dimmed:
                self._dimmed = False
                self._invalidate_sent()
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
                    self._last_sent_brightness = 0
                    self._invalidate_sent()
            self.frame_changed.emit([RgbColor(0, 0, 0)] * self._zone_count)
            return

        frames = self._zone_rgb_list(settings)

        if self._zone_count <= 1:
            self._tick_single_zone(now, frames[0])
        else:
            self._tick_multi_zone(now, frames)

        # Brightness is applied atomically by the daemon's _write_led_color
        # (using the user-brightness synced via set_keyboard_user_brightness),
        # so no separate brightness write is needed here.
        # Always update visual keyboard (responsive UI)
        self.frame_changed.emit(frames)

    def _tick_single_zone(self, now: float, frame: RgbColor) -> None:
        """Original single-zone write path (unchanged behavior)."""
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

    def _tick_multi_zone(self, now: float, frames: list[RgbColor]) -> None:
        """Write each zone that differs from the last successfully sent value."""
        if now - self._last_send < 0.200:
            return

        any_write = False
        for zone, frame in enumerate(frames):
            color = (frame.red, frame.green, frame.blue)
            last = (
                self._last_sent_zone_colors[zone]
                if zone < len(self._last_sent_zone_colors)
                else None
            )
            need = (self._backlight_on is not True) or (color != last)
            if not need:
                continue
            try:
                api.set_keyboard_color(*color, zone=zone)
            except Exception:
                pass
            if zone < len(self._last_sent_zone_colors):
                self._last_sent_zone_colors[zone] = color
            any_write = True

        if any_write or self._backlight_on is not True:
            self._last_send = now
            self._backlight_on = True
