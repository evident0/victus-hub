"""Profile, fan mode, and GPU MUX — Ohman segments on the home page."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from victus_hub import api
from victus_hub.features.gpu.mux import GpuMuxMode, GpuMuxState, read_gpu_mux_state
from victus_hub.app.theme import COLORS, ECO, BALANCED, PERF, ui_font
from victus_hub.backend.nvidia import get_gpu_name
from victus_hub.widgets.seg import Seg, LinkSeg

logger = logging.getLogger(__name__)

PROFILES = [
    ("Eco", "leaf.png", ECO),
    ("Balanced", "scale.png", BALANCED),
    ("Performance", "rocket.png", PERF),
]

FAN_MODES = [
    ("auto", "Auto", "wind.png", ECO, False),
    ("smart", "Smart", "sparkles.png", BALANCED, False),
    ("max", "Max", "flame.png", PERF, False),
    ("custom", "Custom", "fan.png", BALANCED, True),
]

LOCAL_MUX = object()


class ProfileSection(QWidget):
    """Mode pill, fan links, optional MUX segment."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_requested = Signal()

    def __init__(self, parent=None, *, fan_modes: tuple[str, ...] | None = None,
                 gpu_mux: GpuMuxState | None | object = LOCAL_MUX):
        super().__init__(parent)
        self._fan_modes = [m for m in FAN_MODES if fan_modes is None or m[0] in fan_modes]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._selected_profile = 1
        self._selected_fan_mode = "auto"
        self._mux_modes: tuple[GpuMuxMode, ...] = ()
        self._mux_index_by_seg: list[int] = []

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._mode_seg = Seg([p[0] for p in PROFILES], kind="page")
        self._mode_seg.picked.connect(self._on_profile_click)
        self._mode_seg.select(self._selected_profile, False)
        root.addWidget(self._mode_seg)

        fan_row = QHBoxLayout()
        fan_row.setContentsMargins(0, 24, 0, 0)
        fan_row.setSpacing(10)
        fans_lbl = QLabel("Fans")
        fans_lbl.setFont(ui_font(14))
        fans_lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        fan_row.addWidget(fans_lbl, 0, Qt.AlignVCenter)
        fan_row.addStretch()
        self._fan_links = LinkSeg([m[1] for m in self._fan_modes], gap=16, size=14)
        self._fan_links.picked.connect(self._on_fan_link)
        self._fan_links.select(0, False)
        fan_row.addWidget(self._fan_links, 0, Qt.AlignVCenter)
        root.addLayout(fan_row)

        self._curve_row = QWidget()
        curve_l = QHBoxLayout(self._curve_row)
        curve_l.setContentsMargins(0, 8, 0, 0)
        curve_l.addStretch()
        self._curve_link = QLabel("Edit curve")
        self._curve_link.setFont(ui_font(12))
        self._curve_link.setStyleSheet(
            f"color: {COLORS['accent']}; background: transparent;"
        )
        self._curve_link.setCursor(Qt.PointingHandCursor)
        self._curve_link.mousePressEvent = self._on_curve_click  # type: ignore[method-assign]
        curve_l.addWidget(self._curve_link)
        self._curve_row.setVisible(False)
        root.addWidget(self._curve_row)

        self._mux_block = QWidget()
        mux_outer = QVBoxLayout(self._mux_block)
        mux_outer.setContentsMargins(0, 20, 0, 0)
        mux_outer.setSpacing(8)
        mux_row = QHBoxLayout()
        mux_row.setContentsMargins(0, 0, 0, 0)
        mux_lbl = QLabel("Graphics")
        mux_lbl.setFont(ui_font(14))
        mux_lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        mux_row.addWidget(mux_lbl, 0, Qt.AlignVCenter)
        gpu_name = get_gpu_name()
        if gpu_name:
            short = gpu_name.removeprefix("NVIDIA GeForce ")
            gpu_label = QLabel(short)
            gpu_label.setFont(ui_font(12))
            gpu_label.setStyleSheet(
                f"color: {COLORS['sub']}; background: transparent;"
            )
            mux_row.addWidget(gpu_label)
        mux_row.addStretch()
        mux_outer.addLayout(mux_row)
        self._mux_block.hide()
        root.addWidget(self._mux_block)

        self._build_mux_buttons(mux_outer, gpu_mux)

    def _on_curve_click(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.fan_curves_requested.emit()

    def _build_mux_buttons(self, layout: QVBoxLayout,
                           state: GpuMuxState | None | object) -> None:
        self._mux_modes = ()
        self._mux_index_by_seg = []
        self._mux_selected = None
        self._mux_block.hide()

        # Standalone widgets retain a local read; MainWindow supplies the
        # daemon's detected state through the hardware profile.
        if state is LOCAL_MUX:
            state = read_gpu_mux_state()
        names = [m.label for m in state.modes] if state is not None else []
        self._mux_seg = Seg(names, kind="compact", parent=self._mux_block)
        layout.addWidget(self._mux_seg)
        if state is None or not state.modes:
            return

        self._mux_modes = state.modes
        self._mux_selected = state.current_index
        self._mux_index_by_seg = [m.index for m in self._mux_modes]
        self._mux_seg.picked.connect(self._on_mux_seg)
        try:
            sel = self._mux_index_by_seg.index(self._mux_selected)
        except ValueError:
            sel = 0
        self._mux_seg.select(sel, False)
        self._mux_block.show()

    def set_selected_profile(self, index: int):
        self._selected_profile = index
        self._mode_seg.select(index, True)

    def _on_profile_click(self, index: int):
        self.profile_selected.emit(index)

    def set_selected_fan_mode(self, mode: str):
        keys = [m[0] for m in self._fan_modes]
        if mode not in keys:
            return
        self._selected_fan_mode = mode
        self._fan_links.select(keys.index(mode), True)
        self._curve_row.setVisible(mode == "custom")

    def _on_fan_link(self, index: int):
        if index < 0 or index >= len(self._fan_modes):
            return
        mode = self._fan_modes[index][0]
        if mode == self._selected_fan_mode:
            if mode == "custom":
                self.fan_curves_requested.emit()
            return
        self.set_selected_fan_mode(mode)
        self.fan_mode_selected.emit(mode)

    def _on_mux_seg(self, index: int):
        if index < 0 or index >= len(self._mux_modes):
            return
        self._on_mux_click(self._mux_modes[index])

    def refresh_accent(self) -> None:
        self._mode_seg.refresh_accent()
        self._fan_links.refresh_accent()
        self._mux_seg.refresh_accent()
        self._curve_link.setStyleSheet(
            f"color: {COLORS['accent']}; background: transparent;"
        )

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
            try:
                sel = self._mux_index_by_seg.index(self._mux_selected)
            except ValueError:
                sel = 0
            self._mux_seg.select(sel, False)
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
            try:
                sel = self._mux_index_by_seg.index(self._mux_selected)
            except ValueError:
                sel = 0
            self._mux_seg.select(sel, False)
            return

        self._mux_selected = mode.index
        try:
            sel = self._mux_index_by_seg.index(mode.index)
        except ValueError:
            sel = 0
        self._mux_seg.select(sel, True)
