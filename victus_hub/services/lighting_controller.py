"""Keyboard lighting preview timer.

Hardware writes live in the daemon. This controller keeps the on-screen
preview in sync (static color, software effects, idle dim) and pushes
lighting policy to the daemon when settings change.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from victus_hub import api
from victus_hub.features.keyboard.lighting import (
    ANIM_INTERVAL_MS,
    STATIC_INTERVAL_MS,
    RgbColor,
    effect_is_animated,
    lighting_frames,
    normalize_lighting_settings,
    normalize_zone_colors,
    read_lighting_settings,
    step_increment,
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
        self._anim_step = 0.0
        self._last_anim = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms())
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        # Gate the on-screen keyboard-preview repaint only (NOT hardware
        # writes): stop repainting when the window is hidden. Home and
        # Keyboard both show a preview, so this follows window visibility
        # rather than the current tab. Defaults active until MainWindow
        # flips it on show/hide.
        self._ui_active = True

    @property
    def zone_count(self) -> int:
        return self._zone_count

    def shutdown(self):
        """Stop the preview timer. Hardware lighting stays with the daemon."""
        self._timer.stop()

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
        self._last_anim = time.monotonic()
        self._sync_timer()
        self._timer.start()

    def set_ui_active(self, active: bool) -> None:
        """Run the preview timer only while the window is visible."""
        self._ui_active = active
        if active:
            self._sync_timer()
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()

    # ── Settings mutators (called from KeyboardPage signal handlers) ──

    def set_enabled(self, enabled: bool) -> None:
        self._update(enabled=enabled)
        self._sync_timer()

    def set_effect(self, effect: str) -> None:
        self._update(effect=effect)
        self._anim_step = 0.0
        self._last_anim = time.monotonic()
        self._invalidate_sent()
        self._sync_timer()

    def set_speed(self, speed: int) -> None:
        self._update(speed=max(1, min(int(speed), 100)))

    def set_color(self, color: str) -> None:
        """Set the primary color (single-zone, or Color 1 for wave/gradient)."""
        if self._zone_count <= 1:
            self._update(color=color, zone_colors=[])
        else:
            zones = normalize_zone_colors(
                self._settings.color,
                self._settings.zone_colors,
                max(self._zone_count, 4),
            )
            zones[0] = color
            self._update(color=color, zone_colors=zones)
        self._invalidate_sent()

    def set_color2(self, color: str) -> None:
        """Set the secondary color used by wave / gradient."""
        self._update(color2=color)
        self._invalidate_sent()

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
        self._invalidate_sent()

    def set_idle_timeout(self, timeout: int) -> None:
        self._update(idle_timeout=max(0, timeout))
        # If idle timeout was disabled while dimmed, wake the backlight
        if timeout == 0 and self._dimmed:
            self._dimmed = False
            self._invalidate_sent()

    def set_brightness(self, level: int) -> None:
        """Set the user-preferred backlight brightness (0-255)."""
        self._update(brightness=max(0, min(255, level)))

    def apply_remote(self, settings) -> None:
        """Adopt daemon lighting policy without pushing it back."""
        self._settings = settings
        write_lighting_settings(self._settings)
        if self._settings.idle_timeout == 0:
            self._dimmed = False
        self._invalidate_sent()
        self._sync_timer()

    def _update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self._settings, key, value)
        write_lighting_settings(self._settings)
        api.set_lighting_config(self._settings)

    def _invalidate_sent(self) -> None:
        self._last_sent_color = None
        self._last_sent_zone_colors = [None] * self._zone_count

    def _interval_ms(self) -> int:
        settings = normalize_lighting_settings(self._settings, self._zone_count)
        if settings.enabled and effect_is_animated(settings.effect):
            return ANIM_INTERVAL_MS
        return STATIC_INTERVAL_MS

    def _sync_timer(self) -> None:
        interval = self._interval_ms()
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)

    # ── Animation tick ──

    def _tick(self) -> None:
        settings = normalize_lighting_settings(self._settings, self._zone_count)
        now = time.monotonic()
        dt = max(0.0, now - self._last_anim)
        self._last_anim = now
        animated = settings.enabled and effect_is_animated(settings.effect)
        if animated:
            self._anim_step += step_increment(settings.speed, dt)
        else:
            self._anim_step = 0.0

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

        want_off = (not settings.enabled) or self._dimmed
        if want_off:
            if self._ui_active:
                self.frame_changed.emit([RgbColor(0, 0, 0)] * self._zone_count)
            return

        frames = lighting_frames(settings, self._zone_count, self._anim_step)
        if self._ui_active:
            self.frame_changed.emit(frames)
