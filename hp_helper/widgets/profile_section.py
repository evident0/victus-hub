"""Profile selection section with profile buttons and fan mode controls."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from hp_helper.widgets.app_button import AppButton
from hp_helper.theme import COLORS


PROFILES = [
    ("Power Saver", "eco.ico", COLORS["accent_green"]),
    ("Balanced", "standard.ico", COLORS["accent_blue"]),
    ("Performance", "ultimate.ico", COLORS["accent_red"]),
]

FAN_MODES = [
    ("Auto", "icons8-automation-32.png", COLORS["accent_blue"]),
    ("Max", "icons8-fan-48.png", COLORS["accent_red"]),
    ("Custom", "icons8-settings-32.png", COLORS["accent_blue"]),
]


class ProfileSection(QWidget):
    """Profile selection buttons + fan mode control row."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)       # "auto", "max", "custom"
    fan_curves_popout_requested = Signal()

    def __init__(self, hide_title: bool = False, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(9)

        self._selected_profile = 1

        # ── Profile buttons row ──
        profile_row = QHBoxLayout()
        profile_row.setSpacing(9)
        self._profile_buttons: list[AppButton] = []

        for i, (label, icon, accent) in enumerate(PROFILES):
            btn = AppButton(label, icon, accent, selected=(i == self._selected_profile))
            btn.clicked.connect(lambda checked, idx=i: self._on_profile_click(idx))
            profile_row.addWidget(btn)
            self._profile_buttons.append(btn)

        profile_row.addStretch()
        root.addLayout(profile_row)

        # ── Fan mode buttons row ──
        fan_row = QHBoxLayout()
        fan_row.setSpacing(9)
        self._fan_buttons: dict[str, AppButton] = {}
        self._selected_fan_mode = "auto"

        for label, icon, accent in FAN_MODES:
            key = label.lower()
            btn = AppButton(label, icon, accent, selected=(key == self._selected_fan_mode))
            btn.clicked.connect(lambda checked, k=key: self._on_fan_click(k))
            fan_row.addWidget(btn)
            self._fan_buttons[key] = btn

        # Pop-out button next to Custom
        popout_btn = QPushButton("\u2197")  # ↗
        popout_btn.setMinimumSize(36, 72)
        popout_btn.setMaximumWidth(36)
        popout_btn.setCursor(Qt.PointingHandCursor)
        popout_btn.setToolTip("Open fan curves in separate window")
        popout_btn.clicked.connect(self.fan_curves_popout_requested.emit)
        popout_font = QFont()
        popout_font.setPointSize(14)
        popout_btn.setFont(popout_font)
        popout_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['surface']};
                color: {COLORS['text_secondary']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: #2a2a2a;
                color: {COLORS['text']};
            }}
        """)
        fan_row.addWidget(popout_btn)

        fan_row.addStretch()
        root.addLayout(fan_row)

    # ── Profile selection ──

    def set_selected_profile(self, index: int):
        """Update which profile button is selected."""
        self._selected_profile = index
        for i, btn in enumerate(self._profile_buttons):
            btn.set_selected(i == index)

    def _on_profile_click(self, index: int):
        self.profile_selected.emit(index)
    # ── Fan mode ──

    def _on_fan_click(self, mode: str):
        self._selected_fan_mode = mode
        for key, btn in self._fan_buttons.items():
            btn.set_selected(key == mode)
        self.fan_mode_selected.emit(mode)
