"""Event-driven battery profile switching using UPower on the system bus."""

import logging

from PySide6.QtCore import QObject, QSettings, Signal, Slot
from PySide6.QtDBus import (
    QDBusConnection, QDBusMessage, QDBusPendingCallWatcher,
    QDBusServiceWatcher, QDBusVariant,
)

logger = logging.getLogger(__name__)
BATTERY_POWER_SAVE_KEY = "power/save_on_battery"
_SERVICE = "org.freedesktop.UPower"
_PATH = "/org/freedesktop/UPower"
_PROPERTIES = "org.freedesktop.DBus.Properties"


class BatteryPowerController(QObject):
    power_save_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = QSettings().value(BATTERY_POWER_SAVE_KEY, False, type=bool)
        self._on_battery = None
        self._generation = 0
        self._bus = QDBusConnection.systemBus()
        self._service_watcher = QDBusServiceWatcher(
            _SERVICE, self._bus,
            QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange, self,
        )
        self._service_watcher.serviceOwnerChanged.connect(self._owner_changed)
        if not self._bus.connect(
            _SERVICE, _PATH, _PROPERTIES, "PropertiesChanged", "sa{sv}as",
            self, "1_properties_changed(QDBusMessage)",
        ):
            logger.warning("Could not subscribe to UPower battery events")

    @Slot(bool)
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        QSettings().setValue(BATTERY_POWER_SAVE_KEY, enabled)
        self._generation += 1
        self._on_battery = None
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Read once at startup, enable, resume, or a UPower event; no timer."""
        if not self._enabled:
            return
        self._generation += 1
        generation = self._generation
        message = QDBusMessage.createMethodCall(_SERVICE, _PATH, _PROPERTIES, "Get")
        message.setArguments([_SERVICE, "OnBattery"])
        watcher = QDBusPendingCallWatcher(self._bus.asyncCall(message), self)
        watcher.finished.connect(lambda call: self._read_finished(call, generation))

    def _read_finished(self, watcher, generation: int) -> None:
        reply = watcher.reply()
        watcher.deleteLater()
        if generation != self._generation or not self._enabled:
            return
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            logger.warning("Could not read UPower OnBattery: %s", reply.errorMessage())
            return
        args = reply.arguments()
        value = args[0] if args else None
        if isinstance(value, QDBusVariant):
            value = value.variant()
        if not isinstance(value, bool):
            return
        previous = self._on_battery
        self._on_battery = value
        if value and previous is not True:
            self.power_save_requested.emit()

    @Slot(QDBusMessage)
    def _properties_changed(self, message: QDBusMessage) -> None:
        args = message.arguments()
        if args and args[0] == _SERVICE:
            self.refresh()

    @Slot(str, str, str)
    def _owner_changed(self, service: str, old_owner: str, new_owner: str) -> None:
        self._generation += 1
        self._on_battery = None
        if new_owner:
            self.refresh()
