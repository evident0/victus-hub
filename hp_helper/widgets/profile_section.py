"""Profile selection, fan mode, and GPU MUX — labeled control sections."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QFrame,
)
from PySide6.QtCore import Qt, Signal

from hp_helper import api
from hp_helper.widgets.app_button import AppButton
from hp_helper.widgets.segmented_control import SegmentedControl
from hp_helper.features.gpu.mux import GpuMuxMode, read_gpu_mux_state
from hp_helper.app.theme import COLORS
from hp_helper.backend.nvidia import get_gpu_name

logger = logging.getLogger(__name__)

PROFILES = [
    ("Power Saver", "NewIcons/leaf.png", COLORS["accent_green"]),
    ("Balanced", "NewIcons/balance.png", COLORS["accent_blue"]),
    ("Performance", "NewIcons/rocket.png", COLORS["accent_red"]),
]

FAN_MODES = [
    ("auto", "Auto", False),
    ("max", "Max", False),
    ("custom", "Custom", True),
]

_MUX_ACCENTS = (
    COLORS["accent_green"],
    COLORS["accent_blue"],
    COLORS["accent_red"],
    COLORS["accent_blue"],
)

# Icon-above-label tiles for known MUX modes (white monochrome assets)
_MUX_ICONS: dict[str, str] = {
    "hybrid": "NewIcons/hybrid.png",
    "discrete": "NewIcons/discrete.png",
}


def _section_title(title: str) -> QLabel:
    """Short section label only (no description body text)."""
    lbl = QLabel(title)
    lbl.setStyleSheet(f"""
        color: {COLORS['text']};
        font-size: 13px;
        font-weight: 600;
        background: transparent;
    """)
    return lbl


def _section_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {COLORS['border']}; border: none;")
    return line


class ProfileSection(QWidget):
    """Separated control groups: performance, fans, GPU MUX."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, hide_title: bool = False, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(0)

        self._selected_profile = 1
        self._selected_fan_mode = "auto"
        self._mux_modes: tuple[GpuMuxMode, ...] = ()
        self._mux_buttons: dict[int, AppButton] = {}
        self._mux_selected: int | None = None

        # ── Performance profile ──
        root.addWidget(_section_title("Performance Mode"))
        root.addSpacing(10)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(10)
        self._profile_buttons: list[AppButton] = []
        for i, (label, icon, accent) in enumerate(PROFILES):
            btn = AppButton(label, icon, accent, selected=(i == self._selected_profile))
            btn.clicked.connect(lambda _, idx=i: self._on_profile_click(idx))
            profile_row.addWidget(btn, 1)
            self._profile_buttons.append(btn)
        root.addLayout(profile_row)

        root.addSpacing(18)
        root.addWidget(_section_divider())
        root.addSpacing(18)

        # ── Fan mode ──
        root.addWidget(_section_title("Fan Mode"))
        root.addSpacing(10)
        fan_row = QHBoxLayout()
        fan_row.setSpacing(8)
        fan_row.addStretch()
        self._fan_segments = SegmentedControl(FAN_MODES)
        self._fan_segments.setFixedWidth(360)
        self._fan_segments.segment_selected.connect(self._on_fan_select)
        self._fan_segments.action_requested.connect(self._on_fan_action)
        fan_row.addWidget(self._fan_segments)
        fan_row.addStretch()
        root.addLayout(fan_row)

        # ── GPU MUX ──
        self._mux_block = QWidget()
        self._mux_block.setStyleSheet("background: transparent;")
        mux_outer = QVBoxLayout(self._mux_block)
        mux_outer.setContentsMargins(0, 0, 0, 0)
        mux_outer.setSpacing(0)

        mux_outer.addSpacing(18)
        mux_outer.addWidget(_section_divider())
        mux_outer.addSpacing(18)


        mux_title_row = QHBoxLayout()
        mux_title_row.setContentsMargins(0, 0, 0, 0)
        mux_title_row.addWidget(_section_title("MUX Switch"))
        mux_title_row.addStretch()
        gpu_name = get_gpu_name()
        if gpu_name:
            gpu_label = QLabel(gpu_name)
            gpu_label.setStyleSheet(f"""
                color: {COLORS['text_secondary']};
                font-size: 12px;
                font-weight: 400;
                background: transparent;
            """)
            mux_title_row.addWidget(gpu_label, alignment=Qt.AlignRight | Qt.AlignVCenter)
        mux_outer.addLayout(mux_title_row)
        mux_outer.addSpacing(10)

        self._mux_row = QHBoxLayout()
        self._mux_row.setSpacing(10)
        mux_outer.addLayout(self._mux_row)

        self._mux_block.hide()
        root.addWidget(self._mux_block)

        self._build_mux_buttons()

    def _build_mux_buttons(self) -> None:
        while self._mux_row.count():
            item = self._mux_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._mux_buttons.clear()
        self._mux_modes = ()
        self._mux_selected = None
        self._mux_block.hide()

        state = read_gpu_mux_state()
        if state is None or not state.modes:
            return

        self._mux_modes = state.modes
        self._mux_selected = state.current_index
        for i, mode in enumerate(self._mux_modes):
            accent = _MUX_ACCENTS[i % len(_MUX_ACCENTS)]
            icon = _MUX_ICONS.get(mode.name.lower())
            btn = AppButton(
                mode.label,
                icon,
                accent,
                selected=(mode.index == self._mux_selected),
            )
            btn.clicked.connect(lambda _, m=mode: self._on_mux_click(m))
            self._mux_row.addWidget(btn, 1)
            self._mux_buttons[mode.index] = btn
        self._mux_block.show()

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

    # ── GPU MUX ──

    def _on_mux_click(self, mode: GpuMuxMode):
        if mode.index == self._mux_selected:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("MUX Switch")
        box.setText(
            f"Switch to {mode.label}?\n\n"
            "You must restart for this change to take effect."
        )
        cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
        apply_btn = box.addButton("Apply", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel_btn)
        box.exec()

        if box.clickedButton() is not apply_btn:
            return

        try:
            api.set_gpu_mux_mode(mode.index)
        except Exception as exc:
            logger.exception("set gpu mux mode failed")
            QMessageBox.warning(
                self,
                "MUX Switch",
                f"Failed to set GPU MUX mode: {exc}",
            )
            return

        self._mux_selected = mode.index
        for idx, btn in self._mux_buttons.items():
            btn.set_selected(idx == mode.index)
