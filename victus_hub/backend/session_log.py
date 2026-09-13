"""In-memory copy of this session's terminal log lines."""

from __future__ import annotations

import logging
from collections import deque

_MAX_LINES = 4000
_handler: SessionLogHandler | None = None


class SessionLogHandler(logging.Handler):
    """Keep a rolling buffer of formatted log lines for diagnostics."""

    def __init__(self, maxlen: int = _MAX_LINES):
        super().__init__()
        self.records: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            self.handleError(record)


def install() -> None:
    """Attach the session buffer to the root logger (idempotent)."""
    global _handler
    if _handler is not None:
        return
    handler = SessionLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(handler)
    _handler = handler


def lines() -> list[str]:
    """Return a snapshot of captured session log lines."""
    if _handler is None:
        return []
    return list(_handler.records)
