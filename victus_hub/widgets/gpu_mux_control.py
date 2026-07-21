"""GPU MUX mode buttons driven by hp-gpu-mux sysfs."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QFrame, QHBoxLayout, QMessageBox, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from victus_hub import api
from victus_hub.features.gpu.mux import GpuMuxMode, read_gpu_mux_state
from victus_hub.app.theme import COLORS

logger = logging.getLogger(__name__)


class GpuMuxControl(QFrame):
    """Pill-shaped MUX selector with confirmation before writing sysfs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = read_gpu_mux_state()
        self._modes: tuple[GpuMuxMode, ...] = ()
        self._buttons: dict[int, QPushButton] = {}
        self._selected_index: int | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        if self._state is not None:
            self._modes = self._state.modes
            for mode in self._modes:
                btn = QPushButton(mode.label)
                btn.setObjectName("muxBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFont(QFont("", 11, QFont.Bold))
                btn.clicked.connect(lambda _, m=mode: self._on_mode_click(m))
                layout.addWidget(btn, 1)
                self._buttons[mode.index] = btn
            if self._state.current_index is not None:
                self.set_selected(self._state.current_index)

        self.setStyleSheet(self._stylesheet())
        self._refresh_styles()

    def is_available(self) -> bool:
        return bool(self._modes)

    def mode_count(self) -> int:
        return len(self._modes)

    def set_selected(self, index: int):
        """Outline the active MUX mode without writing sysfs."""
        self._selected_index = index
        self._refresh_styles()

    def _on_mode_click(self, mode: GpuMuxMode):
        if mode.index == self._selected_index:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("GPU MUX")
        box.setText(
            f"Do you want to change to {mode.label}? "
            "You must restart to apply changes."
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
                "GPU MUX",
                f"Failed to set GPU MUX mode: {exc}",
            )
            return

        self.set_selected(mode.index)

    def _refresh_styles(self):
        for index, btn in self._buttons.items():
            btn.setProperty("selected", index == self._selected_index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _stylesheet(self) -> str:
        return f"""
            GpuMuxControl {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 18px;
            }}
            #muxBtn {{
                background: transparent;
                border: none;
                border-radius: 14px;
                padding: 8px 12px;
                color: {COLORS['text_secondary']};
            }}
            #muxBtn:hover {{
                color: {COLORS['text']};
            }}
            #muxBtn[selected="true"] {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['accent_blue']};
            }}
        """