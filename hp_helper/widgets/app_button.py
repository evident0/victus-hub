"""Styled push button with accent color support for profile selection."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QSizePolicy
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont

from hp_helper.icon_utils import load_icon
from hp_helper.theme import COLORS


class AppButton(QPushButton):
    """A styled button with icon, label, and accent color border when selected.

    The *icon* parameter is a filename relative to ``resources/icons/``
    for a .png/.ico, or a Unicode string used directly as a text icon.
    """

    def __init__(self, label: str, icon: str, accent: str, selected: bool = False, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon_spec = icon
        self._accent = accent
        self._selected = selected
        self._enabled = True

        self.setMinimumHeight(72)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        # Icon: file path vs unicode text
        image_exts = (".png", ".ico", ".svg", ".bmp", ".jpg", ".jpeg")
        if icon and any(icon.lower().endswith(ext) for ext in image_exts):
            ico = load_icon(icon, size=28)
            if not ico.isNull():
                self.setIcon(ico)
                self.setIconSize(QSize(28, 28))
                self.setText(label)
            else:
                self.setText(f"{icon}\n{label}")
        else:
            self.setText(f"{icon}\n{label}")

        font = QFont()
        font.setPointSize(11)
        self.setFont(font)

        self._update_style()

    def set_selected(self, selected: bool):
        """Update the selected visual state."""
        self._selected = selected
        self._update_style()

    def setEnabled(self, enabled: bool):
        """Override to track enabled state for styling."""
        super().setEnabled(enabled)
        self._enabled = enabled
        self._update_style()

    @property
    def accent(self) -> str:
        return self._accent

    @accent.setter
    def accent(self, value: str):
        self._accent = value
        self._update_style()

    def _update_style(self):
        if not self._enabled:
            color = COLORS["text_secondary"]
            bg = "#242424"
            border = "1px solid #3f3f3f"
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    color: {color};
                    border: {border};
                    border-radius: 6px;
                    padding: 8px 4px;
                }}
            """)
            return

        bg = COLORS["surface"]
        text_color = COLORS["text"]

        if self._selected:
            style = f"""
                QPushButton {{
                    background-color: {bg};
                    color: {self._accent};
                    border: 3px solid {self._accent};
                    border-radius: 6px;
                    padding: 6px 2px;
                }}
                QPushButton:hover {{
                    background-color: #3a3a3a;
                }}
            """
        else:
            style = f"""
                QPushButton {{
                    background-color: {bg};
                    color: {text_color};
                    border: 1px solid {COLORS['border']};
                    border-radius: 6px;
                    padding: 8px 4px;
                }}
                QPushButton:hover {{
                    background-color: #2a2a2a;
                }}
                QPushButton:pressed {{
                    background-color: #3a3a3a;
                }}
            """
        self.setStyleSheet(style)
