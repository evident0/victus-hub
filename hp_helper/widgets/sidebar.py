"""Sidebar navigation widget with tab buttons."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from hp_helper.theme import COLORS


TABS = [
    ("⌂", "Home"),
    ("◎", "Fans & Power"),
    ("▤", "Sensors"),
    ("⌨", "Keyboard"),
]


class Sidebar(QWidget):
    """Vertical sidebar with Unicode icon tab buttons."""

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

        for i, (icon_char, tooltip) in enumerate(TABS):
            btn = QPushButton(icon_char, self)
            btn.setFixedSize(48, 48)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("sidebarBtn")
            font = QFont()
            font.setPointSize(16)
            font.setBold(True)
            btn.setFont(font)
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
