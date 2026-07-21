"""Reusable status badge — colored dot + text label."""

from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QColor


class StatusBadge(QLabel):
    """A small colored label: dot + text.

    Usage::

        badge = StatusBadge("hp-wmi", "#06b48a")
        badge = StatusBadge("no kbd", "#ff2020")
    """

    def __init__(self, text: str, color: str | QColor, parent=None):
        if isinstance(color, QColor):
            color = color.name()
        super().__init__(f"\u25cf {text}", parent)  # ●
        self.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 400; background: transparent;"
        )
