"""Sidebar navigation widget with tab buttons."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt, QSize

from hp_helper.icon_utils import load_icon
TABS = [
    ("icons8-laptop-32.png", "Home"),
    ("icons8-fan-32.png", "Fans & Power"),
    ("icons8-temperature-32.png", "Sensors"),
    ("icons8-keyboard-32.png", "Keyboard"),
    ("icons8-settings-32.png", "Settings"),
]


class Sidebar(QWidget):
    """Vertical sidebar with icon tab buttons."""

    tab_changed = Signal(int)

    # Button size; right margin is 0 so tabs sit flush against content panels.
    _BTN = 56
    _ICON = 28
    _SIDE_MARGIN = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self._SIDE_MARGIN + self._BTN)
        self.setObjectName("sidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._SIDE_MARGIN, 6, 0, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignTop)

        self._buttons: list[QPushButton] = []
        self._active_index = 0

        for i, (icon_file, tooltip) in enumerate(TABS):
            btn = QPushButton(self)
            btn.setIcon(load_icon(icon_file, size=self._ICON))
            btn.setIconSize(QSize(self._ICON, self._ICON))
            btn.setToolTip(tooltip)
            btn.setFixedSize(self._BTN, self._BTN)
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
