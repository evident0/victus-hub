"""Profile selection section with centered profile buttons and fan mode segmented control."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from hp_helper.widgets.app_button import AppButton
from hp_helper.widgets.segmented_control import SegmentedControl
from hp_helper.theme import COLORS


PROFILES = [
    ("Power Saver", None, COLORS["accent_green"]),
    ("Balanced", None, COLORS["accent_blue"]),
    ("Performance", None, COLORS["accent_red"]),
]

FAN_MODES = [
    ("auto", "Auto", False),
    ("max", "Max", False),
    ("custom", "Custom", True),
]


class ProfileSection(QWidget):
    """Centered profile selection buttons + fan mode segmented control."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, hide_title: bool = False, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(12)

        self._selected_profile = 1
        self._selected_fan_mode = "auto"

        # ── Profile buttons (centered) ──
        profile_row = QHBoxLayout()
        profile_row.setSpacing(9)
        profile_row.addStretch()
        self._profile_buttons: list[AppButton] = []
        for i, (label, icon, accent) in enumerate(PROFILES):
            btn = AppButton(label, icon, accent, selected=(i == self._selected_profile))
            btn.clicked.connect(lambda _, idx=i: self._on_profile_click(idx))
            profile_row.addWidget(btn)
            self._profile_buttons.append(btn)
        profile_row.addStretch()
        root.addLayout(profile_row)

        # ── Fan mode segmented control (centered) ──
        fan_row = QHBoxLayout()
        fan_row.addStretch()
        self._fan_segments = SegmentedControl(FAN_MODES)
        self._fan_segments.setFixedWidth(360)
        self._fan_segments.segment_selected.connect(self._on_fan_select)
        self._fan_segments.action_requested.connect(self._on_fan_action)
        fan_row.addWidget(self._fan_segments)
        fan_row.addStretch()
        root.addLayout(fan_row)

    # ── Profile selection ──

    def set_selected_profile(self, index: int):
        self._selected_profile = index
        for i, btn in enumerate(self._profile_buttons):
            btn.set_selected(i == index)

    def _on_profile_click(self, index: int):
        self.profile_selected.emit(index)

    # ── Fan mode ──

    def set_selected_fan_mode(self, mode: str):
        self._selected_fan_mode = mode
        self._fan_segments.set_selected(mode)

    def _on_fan_select(self, mode: str):
        self._selected_fan_mode = mode
        self.fan_mode_selected.emit(mode)

    def _on_fan_action(self, _key: str):
        self.fan_curves_popout_requested.emit()
