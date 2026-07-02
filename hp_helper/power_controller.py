"""Owns the power-limit reapply timer and in-flight flag.

Extracted from MainWindow. On each tick (1s), if reapply is enabled and the
configured interval has elapsed, applies the current limits via `api.apply_power_limits`
on a one-shot daemon thread.
"""

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from hp_helper import api
from hp_helper.power_limits import read_power_enabled, read_power_limit_settings


class PowerLimitController(QObject):
    limits_changed = Signal()  # emitted after a successful apply

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_apply_ms = 0
        self._in_flight = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def shutdown(self):
        self._timer.stop()

    def _tick(self) -> None:
        if not read_power_enabled() or self._in_flight:
            return
        settings = read_power_limit_settings()
        if settings.reapply_seconds <= 0:
            return
        now = time.time() * 1000
        if now - self._last_apply_ms < settings.reapply_seconds * 1000:
            return
        self._last_apply_ms = now
        self._in_flight = True

        def _apply():
            try:
                api.apply_power_limits(
                    settings.stapm_limit,
                    settings.fast_limit,
                    settings.slow_limit,
                )
                self.limits_changed.emit()
            except Exception:
                pass
            finally:
                self._in_flight = False

        threading.Thread(target=_apply, daemon=True, name="power-apply").start()
