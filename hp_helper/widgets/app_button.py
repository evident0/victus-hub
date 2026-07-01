"""Styled push button with accent color support for profile selection."""

from PySide6.QtWidgets import QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from hp_helper.theme import COLORS


class AppButton(QPushButton):
    """A styled button with icon text, label, and accent color border when selected."""

    def __init__(self, label: str, icon: str, accent: str, selected: bool = False, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon = icon
        self._accent = accent
        self._selected = selected
        self._enabled = True

        self.setMinimumHeight(72)
        self.setMinimumWidth(100)
        self.setCursor(Qt.PointingHandCursor)

        self.setText(f"{icon}\n{label}")
        font = QFont()
        font.setPointSize(11)
        self.setFont(font)

        self._update_style()

    def set_selected(self, selected: bool):
        """Update the selected visual state."""
        if self._selected == selected:
            return
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
