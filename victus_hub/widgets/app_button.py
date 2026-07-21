"""Styled button with optional icon-above-label layout for profile tiles."""

from __future__ import annotations

from PySide6.QtWidgets import QToolButton, QSizePolicy
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont

from victus_hub.app.icon_utils import load_icon
from victus_hub.app.theme import COLORS


class AppButton(QToolButton):
    """Selectable tile button.

    When *icon* is an image path, the icon is shown above the label
    (``ToolButtonTextUnderIcon``). Unicode icons fall back to stacked text.
    """

    def __init__(self, label: str, icon: str | None, accent: str,
                 selected: bool = False, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon_spec = icon
        self._accent = accent
        self._selected = selected
        self._enabled = True
        self._has_image_icon = False

        self.setMinimumHeight(88)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(False)
        self.setFocusPolicy(Qt.StrongFocus)

        image_exts = (".png", ".ico", ".svg", ".bmp", ".jpg", ".jpeg")
        if icon and any(icon.lower().endswith(ext) for ext in image_exts):
            ico = load_icon(icon, size=32)
            if not ico.isNull():
                self.setIcon(ico)
                self.setIconSize(QSize(32, 32))
                self.setText(label)
                self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                self._has_image_icon = True
            else:
                self.setText(f"{icon}\n{label}")
                self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        elif icon:
            self.setText(f"{icon}\n{label}")
            self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        else:
            self.setText(label)
            self.setToolButtonStyle(Qt.ToolButtonTextOnly)

        font = QFont("", 11, QFont.Bold)
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
        # Extra top/bottom padding when icon sits above text
        pad = "10px 6px 8px 6px" if self._has_image_icon else "8px 4px"
        pad_selected = "8px 4px 6px 4px" if self._has_image_icon else "6px 2px"

        if not self._enabled:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: #242424;
                    color: {COLORS['text_secondary']};
                    border: 1px solid #3f3f3f;
                    border-radius: 6px;
                    padding: {pad};
                }}
            """)
            return

        bg = COLORS["surface"]
        text_color = COLORS["text"]

        if self._selected:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: {bg};
                    color: {text_color};
                    border: 3px solid {self._accent};
                    border-radius: 6px;
                    padding: {pad_selected};
                }}
                QToolButton:hover {{
                    background-color: #3a3a3a;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: {bg};
                    color: {text_color};
                    border: 1px solid {COLORS['border']};
                    border-radius: 6px;
                    padding: {pad};
                }}
                QToolButton:hover {{
                    background-color: #2a2a2a;
                }}
                QToolButton:pressed {{
                    background-color: #3a3a3a;
                }}
            """)
