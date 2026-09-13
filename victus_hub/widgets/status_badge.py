"""Reusable status badge — colored dot + text label."""

from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QColor

from victus_hub.app.theme import COLORS


class StatusBadge(QLabel):
    """A small colored label: dot + text.

    Usage::

        badge = StatusBadge("hp-wmi", "#06b48a")
        badge = StatusBadge("not supported", "#ff2020")
    """

    def __init__(self, text: str, color: str | QColor, parent=None):
        if isinstance(color, QColor):
            color = color.name()
        super().__init__(f"\u25cf  {text.upper()}", parent)  # ●
        self.setStyleSheet(
            f"color: {color}; font-size: 10px; font-weight: 700; "
            f"background-color: {COLORS['surface']}; border: 1px solid {COLORS['border']}; "
            "border-radius: 8px; padding: 3px 8px;"
        )
