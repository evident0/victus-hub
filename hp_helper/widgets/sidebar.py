"""Sidebar navigation widget with tab buttons."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt, QSize

from hp_helper.icon_utils import load_icon
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
            btn.setIcon(load_icon(icon_file, size=24))
            btn.setIconSize(QSize(24, 24))
            btn.setToolTip(tooltip)
            btn.setFixedSize(48, 48)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("sidebarBtn")
            btn.setProperty("active", i == 0)
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
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.tab_changed.emit(index)
