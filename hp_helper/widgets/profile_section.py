"""Profile selection section with four AppButtons in a row."""

from PySide6.QtWidgets import QWidget, QHBoxLayout
from PySide6.QtCore import Signal, Qt

from hp_helper.widgets.app_button import AppButton
from hp_helper.widgets.section_title import SectionTitle
from hp_helper.theme import COLORS


PROFILES = [
    ("Power Saver", "\u262f", COLORS["accent_green"]),   # ☯ → eco
    ("Balanced", "\u25c7", COLORS["accent_blue"]),         # ◇
    ("Performance", "\u21af", COLORS["accent_red"]),       # ↯
    ("Fans + Power", "\u25ce", COLORS["accent_blue"]),     # ◎
]


class ProfileSection(QWidget):
    """Row of profile selection buttons with section title."""

    profile_selected = Signal(int)

    def __init__(self, hide_title: bool = False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(9)

        self._title: SectionTitle | None = None
        self._buttons: list[AppButton] = []
        self._selected = 1
        self._custom_fan_enabled = False

        for i, (label, icon, accent) in enumerate(PROFILES):
            btn = AppButton(label, icon, accent, selected=(i == self._selected))
            btn.clicked.connect(lambda checked, idx=i: self._on_click(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addStretch()

    def set_selected_profile(self, index: int):
        """Update which profile button is selected."""
        self._selected = index
        for i, btn in enumerate(self._buttons):
            if i < 3:
                btn.set_selected(i == index)
            else:
                # Fans+Power button selected when custom fan is active OR index is 3
                btn.set_selected(i == index or self._custom_fan_enabled)

    def set_custom_fan_enabled(self, enabled: bool):
        """Update Fans+Power button highlight based on custom fan state."""
        self._custom_fan_enabled = enabled
        btn = self._buttons[3]
        if enabled:
            btn.set_selected(True)
            btn.accent = COLORS["accent_blue"]
        else:
            btn.set_selected(False)
            btn.accent = COLORS["text_secondary"]

    def _on_click(self, index: int):
        self.profile_selected.emit(index)
