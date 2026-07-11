"""systemd-logind suspend/shutdown signal watcher with delay inhibitors.

Owns the D-Bus subscription to ``org.freedesktop.login1.Manager`` so HP Helper
can reset the hardware to a safe state (fans → auto, keyboard backlight → 0)
in the brief window *before* the system suspends or shuts down.

A **delay** inhibitor (mode ``"delay"``) makes logind wait until our file
descriptors are released (or ``InhibitDelayMaxSec``, default 5 s) before
proceeding, so the cleanup RPCs reach the daemon before the GUI is frozen.
The writes complete in milliseconds; we release the fds right after, so
suspend/shutdown is not meaningfully delayed.

Falls back silently on non-logind systems: the app still works, just without
power-event cleanup.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusUnixFileDescriptor

logger = logging.getLogger(__name__)

_LOGIN1_SERVICE = "org.freedesktop.login1"
_LOGIN1_PATH = "/org/freedesktop/login1"
_LOGIN1_IFACE = "org.freedesktop.login1.Manager"
_INHIBIT_WHO = "HP Helper"
_INHIBIT_WHY = "Restore fan control to auto and turn keyboard backlight off"


class PowerStateWatcher(QObject):
    """Watches logind ``PrepareForSleep``/``PrepareForShutdown`` and holds
    delay inhibitors so cleanup writes can complete before the system goes
    down."""

    # Emitted immediately before suspend (after inhibitor is released, logind
    # proceeds to suspend).
    suspending = Signal()
    # Emitted on resume.
    resuming = Signal()
    # Emitted immediately before shutdown.
    shutting_down = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = QDBusConnection.systemBus()
        if not self._bus.isConnected():
            logger.warning(
                "power_state: no system D-Bus connection; "
                "suspend/shutdown cleanup disabled"
            )
            self._sleep_fd: QDBusUnixFileDescriptor | None = None
            self._shutdown_fd: QDBusUnixFileDescriptor | None = None
            return

        self._sleep_fd = self._acquire_inhibitor("sleep")
        self._shutdown_fd = self._acquire_inhibitor("shutdown")

        # Subscribe to the two logind signals. The slot string must use the
        # Qt internal slot marker "1" prefix (see Qt SLOT() macro).
        try:
            ok = self._bus.connect(
                _LOGIN1_SERVICE, _LOGIN1_PATH, _LOGIN1_IFACE,
                "PrepareForSleep", "b", self, "1_on_prepare_for_sleep(bool)",
            )
            if not ok:
                logger.warning(
                    "power_state: connect PrepareForSleep failed: %s",
                    self._bus.lastError().message(),
                )
            ok = self._bus.connect(
                _LOGIN1_SERVICE, _LOGIN1_PATH, _LOGIN1_IFACE,
                "PrepareForShutdown", "b", self, "1_on_prepare_for_shutdown(bool)",
            )
            if not ok:
                logger.warning(
                    "power_state: connect PrepareForShutdown failed: %s",
                    self._bus.lastError().message(),
                )
        except Exception:
            logger.exception("power_state: D-Bus signal subscription failed")

    # ── Inhibitor helpers ──

    def _acquire_inhibitor(self, what: str) -> QDBusUnixFileDescriptor | None:
        """Call logind ``Inhibit(what, who, why, "delay")`` and return the
        delay-inhibitor file descriptor. ``None`` on failure."""
        try:
            msg = QDBusMessage.createMethodCall(
                _LOGIN1_SERVICE, _LOGIN1_PATH, _LOGIN1_IFACE, "Inhibit"
            )
            msg << what
            msg << _INHIBIT_WHO
            msg << _INHIBIT_WHY
            msg << "delay"
            reply = self._bus.call(msg, QDBus.Block, 5000)
            if reply.type() != QDBusMessage.MessageType.ReplyMessage:
                logger.warning(
                    "power_state: Inhibit(%s) failed: %s %s",
                    what, reply.errorName(), reply.errorMessage(),
                )
                return None
            args = reply.arguments()
            if not args:
                logger.warning("power_state: Inhibit(%s) returned no fd", what)
                return None
            fd = args[0]
            if isinstance(fd, QDBusUnixFileDescriptor) and fd.isValid():
                logger.info("power_state: acquired delay inhibitor (%s)", what)
                return fd
            logger.warning("power_state: Inhibit(%s) returned invalid fd", what)
            return None
        except Exception:
            logger.exception("power_state: Inhibit(%s) call failed", what)
            return None

    def _release_sleep_fd(self) -> None:
        """Release the sleep delay inhibitor so logind proceeds with suspend."""
        if self._sleep_fd is not None:
            try:
                self._sleep_fd.takeFileDescriptor()  # drops the held fd
            except Exception:
                pass
            self._sleep_fd = None

    def _release_shutdown_fd(self) -> None:
        """Release the shutdown delay inhibitor."""
        if self._shutdown_fd is not None:
            try:
                self._shutdown_fd.takeFileDescriptor()
            except Exception:
                pass
            self._shutdown_fd = None

    # ── D-Bus signal slots ──

    @Slot(bool)
    def _on_prepare_for_sleep(self, active: bool) -> None:
        if active:
            logger.info("power_state: PrepareForSleep(True) — suspending")
            self.suspending.emit()
            # Slots are synchronous for direct connections, so the cleanup
            # writes (connected to suspending) have completed by the time
            # emit() returns. Release the sleep inhibitor now so logind
            # proceeds with suspend. The shutdown inhibitor stays held.
            self._release_sleep_fd()
        else:
            logger.info("power_state: PrepareForSleep(False) — resuming")
            # Re-acquire the sleep inhibitor that was released on suspend.
            if self._bus.isConnected() and self._sleep_fd is None:
                self._sleep_fd = self._acquire_inhibitor("sleep")
            self.resuming.emit()

    @Slot(bool)
    def _on_prepare_for_shutdown(self, active: bool) -> None:
        if active:
            logger.info("power_state: PrepareForShutdown(True) — shutting down")
            self.shutting_down.emit()
            self._release_shutdown_fd()