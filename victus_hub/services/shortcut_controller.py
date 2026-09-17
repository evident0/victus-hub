"""Global shortcuts driven by the daemon's push stream, without key polling."""

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QSettings, Qt, Signal
from PySide6.QtNetwork import QLocalSocket

from victus_hub.backend.daemon_client import SOCKET_PATH
from victus_hub.features.keyboard.shortcut import (
    HARDWARE_SHORTCUTS_KEY, KEY_LEFTCTRL, KEY_LEFTSHIFT, read_keybind_settings,
)

logger = logging.getLogger(__name__)


class ShortcutController(QObject):
    triggered = Signal()
    captured = Signal(object, int)
    brightness_step = Signal(int)
    animation_step = Signal(int)
    performance_cycle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = read_keybind_settings()
        self._hardware_enabled = QSettings().value(HARDWARE_SHORTCUTS_KEY, False, type=bool)
        self._capturing = False
        self._subscribed = False
        self._closed = False
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._subscribe)
        self._socket.readyRead.connect(self._read_events)
        self._socket.errorOccurred.connect(self._socket_error)
        # Reconnect only after Qt has finished tearing down the old socket.
        self._socket.disconnected.connect(self._disconnected, Qt.QueuedConnection)
        # Directory notifications reconnect after daemon startup/restart. No
        # retry timer or repeated requests while the daemon is unavailable.
        self._paths = QFileSystemWatcher(self)
        self._paths.directoryChanged.connect(self._connect)
        self._connect()

    def _connect(self, _path=None) -> None:
        if self._closed:
            return
        directory = Path(SOCKET_PATH).parent
        for path in (directory.parent, directory):
            if path.is_dir() and str(path) not in self._paths.directories():
                self._paths.addPath(str(path))
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(SOCKET_PATH)

    def _subscribe(self) -> None:
        self._subscribed = False
        self._socket.write(b"keyboard-events\n")

    def _disconnected(self) -> None:
        subscribed = self._subscribed
        self._subscribed = False
        if subscribed:
            self._connect()

    def _socket_error(self, _error) -> None:
        logger.warning("Shortcut event stream: %s", self._socket.errorString())

    def reload_settings(self) -> None:
        self._settings = read_keybind_settings()
        self._connect()

    def set_hardware_enabled(self, enabled: bool) -> None:
        self._hardware_enabled = enabled
        QSettings().setValue(HARDWARE_SHORTCUTS_KEY, enabled)
        if enabled:
            self._connect()

    def start_capture(self) -> None:
        # Discard buffered presses from before the user clicked Set.
        self._read_events(discard=True)
        self._capturing = True
        self._connect()

    def cancel_capture(self) -> None:
        self._capturing = False

    def is_capturing(self) -> bool:
        return self._capturing

    def shutdown(self) -> None:
        self._closed = True
        self._subscribed = False
        self._paths.blockSignals(True)
        self._socket.blockSignals(True)
        self._socket.abort()

    def _read_events(self, discard: bool = False) -> None:
        while self._socket.canReadLine():
            line = bytes(self._socket.readLine()).decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("ERR"):
                logger.warning("Shortcut event stream unavailable: %s; restart victus-hubd", line)
                continue
            if line == "OK\tkeyboard-events":
                self._subscribed = True
                continue
            if discard or not line.startswith("KEY\t"):
                continue
            try:
                _, raw_mods, raw_key, raw_seq = line.split("\t")
                mods = frozenset(int(m) for m in raw_mods.split(",") if m)
                key = int(raw_key)
                int(raw_seq)
            except ValueError:
                logger.warning("Malformed keyboard event")
                continue
            self._handle_key(mods, key)

    def _handle_key(self, mods: frozenset[int], key: int) -> None:
        if key == 0:
            return
        if self._capturing:
            self._capturing = False
            self.captured.emit(mods, key)
            return
        if self._hardware_enabled and mods == frozenset({KEY_LEFTCTRL, KEY_LEFTSHIFT}):
            if key in (103, 108):  # Up / Down
                self.brightness_step.emit(1 if key == 103 else -1)
                return
            if key in (105, 106):  # Left / Right
                self.animation_step.emit(1 if key == 106 else -1)
                return
            if key == 50:  # M
                self.performance_cycle.emit()
                return
        if self._settings.enabled and key == self._settings.key \
                and mods == frozenset(self._settings.mods):
            self.triggered.emit()
