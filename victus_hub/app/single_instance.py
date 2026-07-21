"""Single-instance guard for Victus Hub.

The first process to start claims a named local socket (``QLocalServer``)
and becomes the *primary* instance. Any subsequent launch connects to that
socket as a ``QLocalSocket``, writes a ``raise`` request, and exits
immediately — the running primary receives the message and shows/raises all
of its windows.

A stale socket file left behind by a crashed primary is cleaned up via
``QLocalServer.removeServer`` before listening.
"""

from __future__ import annotations

import logging
import os
import tempfile

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


def default_socket_path() -> str:
    """Absolute AF_UNIX socket path under XDG_RUNTIME_DIR (fallback /tmp)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, "victus-hub-single-instance.sock")


class SingleInstanceGuard(QObject):
    """Owns the single-instance socket for the lifetime of the primary app."""

    raise_requested = Signal()

    def __init__(self, socket_path: str, parent: QObject | None = None):
        super().__init__(parent)
        self._socket_path = socket_path
        self._server: QLocalServer | None = None
        # True when a raise request landed before any slot was wired up;
        # flushed once by the owner after connecting to ``raise_requested``.
        self._pending_raise = False

    def claim(self) -> bool:
        """Try to become the primary instance.

        Returns ``True`` if this process now owns the server (primary).
        Returns ``False`` if another instance is already running — in that
        case a ``raise`` request has already been sent to it and the caller
        should exit promptly.
        """
        probe = QLocalSocket()
        probe.connectToServer(self._socket_path)
        if probe.waitForConnected(500):
            # Another instance is running: notify it and bail out.
            probe.write(b"raise\n")
            probe.waitForBytesWritten(1000)
            probe.disconnectFromServer()
            if probe.state() != QLocalSocket.UnconnectedState:
                probe.waitForDisconnected(1000)
            probe.deleteLater()
            logger.info("Another Victus Hub instance is running; asked it to raise.")
            return False

        # No existing instance — start the server.
        QLocalServer.removeServer(self._socket_path)
        self._server = QLocalServer()
        # Restrict access to the owning user.
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self._server.listen(self._socket_path):
            logger.error(
                "Single-instance server failed to listen on %s: %s",
                self._socket_path, self._server.errorString(),
            )
            # Continue as primary regardless so the app still runs.
            return True
        self._server.newConnection.connect(self._on_new_connection)
        return True

    def flush_pending(self) -> None:
        """Re-emit a raise request that arrived before a slot was connected."""
        if self._pending_raise:
            self._pending_raise = False
            self.raise_requested.emit()

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection() if self._server else None
        if conn is None:
            return
        conn.waitForReadyRead(1000)
        data = bytes(conn.readAll())
        conn.disconnectFromServer()
        if conn.state() != QLocalSocket.UnconnectedState:
            conn.waitForDisconnected(1000)
        conn.deleteLater()
        if b"raise" in data:
            self._pending_raise = True
            self.raise_requested.emit()
