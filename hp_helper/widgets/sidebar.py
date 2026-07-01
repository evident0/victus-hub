"""Sidebar navigation widget with tab buttons."""

from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt, QSize
from PySide6.QtGui import QIcon

from hp_helper.theme import COLORS

_RES_DIR = Path(__file__).parent.parent / "resources" / "icons"

TABS = [
    ("icons8-laptop-32.png", "Home"),
    ("icons8-fan-32.png", "Fans & Power"),
    ("icons8-temperature-32.png", "Sensors"),
    ("icons8-keyboard-32.png", "Keyboard"),
]


class Sidebar(QWidget):
    """Vertical sidebar with icon tab buttons."""

    tab_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(56)
        self.setObjectName("sidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignTop)

        self._buttons: list[QPushButton] = []
        self._active_index = 0

        for i, (icon_file, tooltip) in enumerate(TABS):
            btn = QPushButton(self)
            icon_path = _RES_DIR / icon_file
            if icon_path.exists():
                btn.setIcon(QIcon(str(icon_path)))
                btn.setIconSize(QSize(24, 24))
            btn.setToolTip(tooltip)
            btn.setFixedSize(48, 48)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("sidebarBtn")
            btn.setStyleSheet(self._btn_style(i == 0))
            btn.clicked.connect(lambda checked, idx=i: self.set_active(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addStretch()

    def set_active(self, index: int):
        """Set the active tab index and update button styles."""
        if index == self._active_index:
            return
        self._active_index = index
        for i, btn in enumerate(self._buttons):
            btn.setStyleSheet(self._btn_style(i == index))
            btn.setProperty("active", i == index)
        self.tab_changed.emit(index)

    def _btn_style(self, active: bool) -> str:
        bg = COLORS["surface"]
        color = COLORS["accent_blue"] if active else COLORS["text_secondary"]
        border = f"border-left: 2px solid {COLORS['accent_blue']};" if active else ""
        return f"""
            QPushButton {{
                background: {bg};
                color: {color};
                border: none;
                border-radius: 6px;
                {border}
                padding: 0;
            }}
            QPushButton:hover {{
                background: #2a2a2a;
                color: {'#ffffff' if not active else COLORS['accent_blue']};
            }}
        """
