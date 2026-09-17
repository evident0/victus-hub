"""Category-based terminal verbosity, independent of the diagnostics buffer."""

import logging
import os
import re


def message_debug_level(text: str) -> int | None:
    """Classify existing subsystem names, command names and hardware paths."""
    text = text.lower()
    if re.search(r"keyboard|kbd|shortcut", text):
        return 3
    if re.search(r"power|ryzenadj|rapl|cpu.frequency|scaling_(min|max)_freq", text):
        return 2
    if re.search(r"\bfans?\b|fan_control|pwm", text):
        return 1
    return None


class TerminalDebugFilter(logging.Filter):
    def __init__(self, level: int):
        super().__init__()
        if level not in range(4):
            raise ValueError("debug level must be 0, 1, 2 or 3")
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            return True
        category = getattr(record, "debug_level", None)
        if category is None:
            category = message_debug_level(f"{record.name} {record.getMessage()}")
        return category is not None and 0 < category <= self.level


def configure_terminal_logging() -> None:
    raw_level = os.environ.get("VICTUS_HUB_DEBUG_LEVEL", "0")
    if raw_level not in ("0", "1", "2", "3"):
        raise ValueError("VICTUS_HUB_DEBUG_LEVEL must be 0, 1, 2 or 3")
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(TerminalDebugFilter(int(raw_level)))
    # Filter only the terminal; diagnostics still receives the full session log.
    logging.basicConfig(level=logging.DEBUG, handlers=[handler])
