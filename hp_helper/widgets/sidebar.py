"""Modern labeled sidebar navigation."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt, QSize

from hp_helper.app.icon_utils import load_icon
from hp_helper.app.theme import COLORS

# (icon file, short label shown in the rail)
TABS = [
    ("NewIcons/laptop.png", "Home"),
    ("NewIcons/fan.png", "Fans"),
    ("NewIcons/keyboard.png", "Keyboard"),
    ("NewIcons/temperature.png", "Sensors"),
    ("icons8-show-right-side-panel-48-white.png", "Processes"),
    ("NewIcons/settings.png", "Settings"),
]


class Sidebar(QWidget):
    """Vertical nav rail with icon + panel name for each tab."""

    tab_changed = Signal(int)

    _WIDTH = 168
    _ICON = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self._WIDTH)
        self.setObjectName("sidebar")
        self.setStyleSheet(f"""
            #sidebar {{
                background-color: {COLORS['surface']};
                border-right: 1px solid {COLORS['border']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignTop)

        self._buttons: list[QPushButton] = []
        self._active_index = 0

        for i, (icon_file, label) in enumerate(TABS):
            btn = QPushButton(f"  {label}")
            btn.setIcon(load_icon(icon_file, size=self._ICON))
            btn.setIconSize(QSize(self._ICON, self._ICON))
            btn.setToolTip(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("sidebarBtn")
            btn.setProperty("active", i == 0)
            btn.setFixedHeight(42)
            btn.setStyleSheet(self._btn_style(i == 0))
            btn.clicked.connect(lambda checked=False, idx=i: self.set_active(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addStretch()

    def _btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton#sidebarBtn {{
                    background-color: rgba(58, 174, 239, 0.14);
                    color: {COLORS['accent_blue']};
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                    padding: 0 12px;
                    font-size: 13px;
                    font-weight: 600;
                }}
            """
        return f"""
            QPushButton#sidebarBtn {{
                background-color: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                border-radius: 10px;
                text-align: left;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#sidebarBtn:hover {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['text']};
            }}
        """

    def set_active(self, index: int):
        """Set the active tab index and update button styles."""
        if index == self._active_index:
            return
        self._active_index = index
        for i, btn in enumerate(self._buttons):
            active = i == index
            btn.setProperty("active", active)
            btn.setStyleSheet(self._btn_style(active))
        self.tab_changed.emit(index)
