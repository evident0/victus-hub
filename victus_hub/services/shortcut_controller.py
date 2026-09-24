"""Capture program keybinds in the UI and receive daemon lighting changes."""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, QSettings, Qt, Signal
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from victus_hub.backend.daemon_client import SOCKET_PATH
from victus_hub.backend.shortcut_policy import MODIFIERS, validate_shortcut
from victus_hub.features.keyboard.lighting import lighting_from_dict
from victus_hub.features.keyboard.shortcut import HARDWARE_SHORTCUTS_KEY

logger = logging.getLogger(__name__)


class ShortcutController(QObject):
    captured = Signal(object, int)
    lighting_changed = Signal(object)
    capture_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hardware_enabled = QSettings().value(HARDWARE_SHORTCUTS_KEY, False, type=bool)
        self._capturing = False
        self._subscribed = False
        self._closed = False
        self._capture_mods = set()
        QApplication.instance().installEventFilter(self)
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
        # Also retry after unlock/login if a daemon restart happened while this
        # session was unauthorized. logind replaces its session-state files.
        for path in (directory.parent, directory, Path("/run/systemd/sessions")):
            if path.is_dir() and str(path) not in self._paths.directories():
                self._paths.addPath(str(path))
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(SOCKET_PATH)

    def _subscribe(self) -> None:
        self._subscribed = False
        self._socket.write(b"shortcut-events\n")

    def _disconnected(self) -> None:
        subscribed = self._subscribed
        self._subscribed = False
        if subscribed:
            self._connect()

    def _socket_error(self, _error) -> None:
        logger.warning("Shortcut event stream: %s", self._socket.errorString())

    def set_hardware_enabled(self, enabled: bool) -> None:
        self._hardware_enabled = enabled
        QSettings().setValue(HARDWARE_SHORTCUTS_KEY, enabled)
        if enabled:
            self._connect()

    def start_capture(self) -> None:
        # Discard buffered presses from before the user clicked Set.
        self._read_events(discard=True)
        self._capturing = True
        self._capture_mods.clear()

    def cancel_capture(self) -> None:
        self._capturing = False
        self._capture_mods.clear()

    def is_capturing(self) -> bool:
        return self._capturing

    def shutdown(self) -> None:
        self._closed = True
        self._subscribed = False
        self._paths.blockSignals(True)
        self._socket.blockSignals(True)
        self._socket.abort()
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event):
        # Capture only events delivered to this application while it has focus.
        # Never ask the root daemon to capture arbitrary keys globally.
        if not self._capturing:
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.cancel_capture()
            self.capture_error.emit("Shortcut capture cancelled when the app lost focus")
            return False
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if event.isAutoRepeat():
            return True
        # Qt's xcb and Wayland plugins expose XKB keycodes (evdev + 8).
        key = int(event.nativeScanCode()) - 8
        if key <= 0:
            return False
        if key in MODIFIERS:
            if event.type() == QEvent.Type.KeyPress:
                self._capture_mods.add(key)
            else:
                self._capture_mods.discard(key)
            return True
        if event.type() == QEvent.Type.KeyRelease:
            return True
        mods = set(self._capture_mods)
        for flag, candidates, fallback in (
            (Qt.ControlModifier, {29, 97}, 29),
            (Qt.ShiftModifier, {42, 54}, 42),
            (Qt.AltModifier, {56, 100}, 56),
            (Qt.MetaModifier, {125, 126}, 125),
        ):
            if event.modifiers() & flag and not mods & candidates:
                mods.add(fallback)
        try:
            mods, key = validate_shortcut(tuple(mods), key)
        except RuntimeError as error:
            self.capture_error.emit(str(error))
            self.cancel_capture()
            return True
        self.cancel_capture()
        self.captured.emit(frozenset(mods), key)
        return True

    def _read_events(self, discard: bool = False) -> None:
        while self._socket.canReadLine():
            line = bytes(self._socket.readLine()).decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("ERR"):
                logger.warning("Shortcut event stream unavailable: %s; restart victus-hubd", line)
                continue
            if line == "OK\tshortcut-events":
                self._subscribed = True
                continue
            if line.startswith("LIGHTING\t"):
                if discard:
                    continue
                try:
                    payload = json.loads(line.split("\t", 1)[1])
                except (json.JSONDecodeError, IndexError):
                    logger.warning("Malformed lighting event")
                    continue
                if isinstance(payload, dict):
                    self.lighting_changed.emit(lighting_from_dict(payload))
                continue
