"""Freedesktop D-Bus application activation and single-instance ownership."""

import logging
import sys

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage

BUS_NAME = "io.github.evident0.VictusHub"
OBJECT_PATH = "/io/github/evident0/VictusHub"
INTERFACE = "org.freedesktop.Application"
logger = logging.getLogger(__name__)


def request_activation(bus: QDBusConnection) -> bool:
    message = QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, "Activate")
    message.setArguments([{}])
    reply = bus.call(message, QDBus.CallMode.Block, 10000)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        logger.warning("Could not activate existing Victus Hub: %s", reply.errorMessage())
        return False
    return True


def notify_existing_instance(stream=None) -> None:
    """Only a command entered in a terminal needs an explanation."""
    stream = stream or sys.stderr
    if stream.isatty():
        print("Victus Hub is already running; showing the existing window. "
              "To see logs here, quit it from the tray and run `victus-hub` again.",
              file=stream)


@ClassInfo({"D-Bus Interface": INTERFACE})
class ApplicationService(QObject):
    activate_requested = Signal()

    def __init__(self):
        super().__init__()
        self._pending = False
        self._ready = False
    def _activate(self) -> None:
        if self._ready:
            self.activate_requested.emit()
        else:
            self._pending = True

    def flush_pending(self) -> None:
        self._ready = True
        if self._pending:
            self._pending = False
            self.activate_requested.emit()

    @Slot("QVariantMap")
    def Activate(self, _platform_data) -> None:
        self._activate()

    @Slot("QStringList", "QVariantMap")
    def Open(self, _uris, _platform_data) -> None:
        self._activate()

    @Slot(str, "QVariantList", "QVariantMap")
    def ActivateAction(self, _name, _parameter, _platform_data) -> None:
        self._activate()
