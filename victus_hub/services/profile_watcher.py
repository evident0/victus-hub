"""Event-driven system power-profile watcher."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusVariant

from victus_hub.backend import profiles

logger = logging.getLogger(__name__)

_TUNED_SERVICE = "com.redhat.tuned"
_TUNED_PATH = "/Tuned"
_TUNED_IFACE = "com.redhat.tuned.control"

_POWER_PROFILES_ENDPOINTS = (
    ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
)
_DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

_FALLBACK_INTERVAL_MS = 30_000
_DBUS_TIMEOUT_MS = 2_000


class ProfileWatcher(QObject):
    """Publish profile changes from the system power-management service."""

    profile_changed = Signal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = QDBusConnection.systemBus()
        self._backend: str | None = None
        self._started = False
        self._tuned_signal_connected = False
        self._power_profiles_signal_connected = False
        self._power_profiles_endpoint = _POWER_PROFILES_ENDPOINTS[0]

        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(_FALLBACK_INTERVAL_MS)
        self._fallback_timer.timeout.connect(self._fallback_refresh)

    def start(self) -> None:
        """Subscribe to the active backend and publish its initial profile."""
        if self._started:
            return
        self._started = True

        if not self._bus.isConnected():
            logger.warning("profile watcher: system D-Bus is unavailable")
            self._publish_cli_profile()
            self._start_fallback()
            return

        if self._try_tuned():
            return
        if self._try_power_profiles_daemon():
            return

        logger.warning("profile watcher: no D-Bus profile backend available")
        self._publish_cli_profile()
        self._start_fallback()

    def stop(self) -> None:
        """Stop fallback polling and remove D-Bus subscriptions."""
        self._fallback_timer.stop()
        if self._tuned_signal_connected:
            self._bus.disconnect(
                _TUNED_SERVICE, _TUNED_PATH, _TUNED_IFACE,
                "profile_changed", "sbs", self,
                "1_on_tuned_profile_changed(QString,bool,QString)",
            )
            self._tuned_signal_connected = False
        if self._power_profiles_signal_connected:
            service, path = self._power_profiles_endpoint
            self._bus.disconnect(
                service, path, _DBUS_PROPERTIES,
                "PropertiesChanged", "sa{sv}as", self,
                "1_on_power_profiles_changed(QDBusMessage)",
            )
            self._power_profiles_signal_connected = False

    def _try_tuned(self) -> bool:
        connected = self._bus.connect(
            _TUNED_SERVICE, _TUNED_PATH, _TUNED_IFACE,
            "profile_changed", "sbs", self,
            "1_on_tuned_profile_changed(QString,bool,QString)",
        )
        if not connected:
            logger.debug("profile watcher: tuned signal subscription failed")
            return False

        name = self._call_string(
            _TUNED_SERVICE, _TUNED_PATH, _TUNED_IFACE, "active_profile",
        )
        index = profiles.profile_index_for_name(name) if name else None
        if index is None:
            self._bus.disconnect(
                _TUNED_SERVICE, _TUNED_PATH, _TUNED_IFACE,
                "profile_changed", "sbs", self,
                "1_on_tuned_profile_changed(QString,bool,QString)",
            )
            return False

        self._tuned_signal_connected = True
        self._backend = "tuned"
        self._fallback_timer.stop()
        self._publish(index, name, "tuned D-Bus")
        logger.info("profile watcher: using tuned D-Bus events")
        return True

    def _try_power_profiles_daemon(self) -> bool:
        for endpoint in _POWER_PROFILES_ENDPOINTS:
            self._power_profiles_endpoint = endpoint
            if self._try_power_profiles_endpoint():
                return True
        return False

    def _try_power_profiles_endpoint(self) -> bool:
        service, path = self._power_profiles_endpoint
        connected = self._bus.connect(
            service, path, _DBUS_PROPERTIES,
            "PropertiesChanged", "sa{sv}as", self,
            "1_on_power_profiles_changed(QDBusMessage)",
        )
        if not connected:
            logger.debug(
                "profile watcher: power-profiles-daemon signal subscription failed",
            )
            return False

        name = self._read_power_profiles_profile()
        index = profiles.profile_index_for_name(name) if name else None
        if index is None:
            self._bus.disconnect(
                service, path, _DBUS_PROPERTIES,
                "PropertiesChanged", "sa{sv}as", self,
                "1_on_power_profiles_changed(QDBusMessage)",
            )
            return False

        self._power_profiles_signal_connected = True
        self._backend = "power-profiles-daemon"
        self._fallback_timer.stop()
        self._publish(index, name, "power-profiles-daemon D-Bus")
        logger.info("profile watcher: using power-profiles-daemon D-Bus events")
        return True

    def _call_string(
        self, service: str, path: str, interface: str, method: str,
    ) -> str | None:
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        reply = self._bus.call(message, QDBus.Block, _DBUS_TIMEOUT_MS)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            logger.debug(
                "profile watcher: %s.%s failed: %s",
                interface, method, reply.errorMessage(),
            )
            return None
        args = reply.arguments()
        if not args or not isinstance(args[0], str):
            return None
        return args[0].strip()

    def _read_power_profiles_profile(self) -> str | None:
        service, path = self._power_profiles_endpoint
        message = QDBusMessage.createMethodCall(
            service, path,
            _DBUS_PROPERTIES, "Get",
        )
        message.setArguments([service, "ActiveProfile"])
        reply = self._bus.call(message, QDBus.Block, _DBUS_TIMEOUT_MS)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        args = reply.arguments()
        if not args:
            return None
        value = args[0]
        if isinstance(value, QDBusVariant):
            value = value.variant()
        return value.strip() if isinstance(value, str) else None

    def _publish_cli_profile(self) -> None:
        """Use the existing CLI path only for startup/slow fallback recovery."""
        reading = profiles.current_profile_reading()
        index = profiles.profile_index_for_name(reading.value)
        if index is not None:
            self._publish(index, reading.value, f"{reading.source} fallback")

    def _publish(self, index: int, name: str, source: str) -> None:
        self.profile_changed.emit(index, name, source)

    def _start_fallback(self) -> None:
        if not self._fallback_timer.isActive():
            self._fallback_timer.start()

    def _fallback_refresh(self) -> None:
        if self._backend is not None:
            self._fallback_timer.stop()
            return
        if self._try_tuned() or self._try_power_profiles_daemon():
            return
        self._publish_cli_profile()

    @Slot(str, bool, str)
    def _on_tuned_profile_changed(
        self, name: str, result: bool, error: str,
    ) -> None:
        if not result:
            logger.warning("profile watcher: tuned profile change failed: %s", error)
            return
        index = profiles.profile_index_for_name(name)
        if index is None:
            logger.warning("profile watcher: unknown tuned profile %r", name)
            return
        self._publish(index, name, "tuned D-Bus")

    @Slot(QDBusMessage)
    def _on_power_profiles_changed(self, message: QDBusMessage) -> None:
        args = message.arguments()
        if args and args[0] != self._power_profiles_endpoint[0]:
            return
        name = self._read_power_profiles_profile()
        index = profiles.profile_index_for_name(name) if name else None
        if index is not None:
            self._publish(index, name, "power-profiles-daemon D-Bus")
