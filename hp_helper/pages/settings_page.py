"""Settings page — fan control constants."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox,
)
from PySide6.QtCore import Qt

from hp_helper import api
from hp_helper.theme import COLORS
from hp_helper.backend.types import FanConfig


def make_spin(label: str, suffix: str, value: int,
              vmin: int, vmax: int) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
    row.addWidget(lbl)
    row.addStretch()
    spin = QSpinBox()
    spin.setRange(vmin, vmax)
    spin.setValue(value)
    spin.setSuffix(f" {suffix}")
    spin.setFixedWidth(90)
    row._spin = spin
    row.addWidget(spin)
    return row


def make_double_spin(label: str, suffix: str, value: float,
                     vmin: float, vmax: float, step: float) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
    row.addWidget(lbl)
    row.addStretch()
    spin = QDoubleSpinBox()
    spin.setRange(vmin, vmax)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    spin.setSuffix(f" {suffix}")
    spin.setFixedWidth(90)
    row._spin = spin
    row.addWidget(spin)
    return row

class SettingsPage(QWidget):
    """Settings tab for fan-control tuning constants."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        cfg = api.get_fan_config()

        # ── Ramp-down delay ──
        ramp_label = QLabel("Fan ramp-down delay")
        ramp_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;")
        layout.addWidget(ramp_label)

        self._ramp_delay = make_spin(
            "Ramp-down delay", "s",
            int(cfg.ramp_down_delay), 0, 120,
        )
        layout.addLayout(self._ramp_delay)

        # ── Separator ──
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        layout.addWidget(sep)

        # ── Fan control constants ──
        constants_label = QLabel("Fan control constants")
        constants_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;")
        layout.addWidget(constants_label)

        self._temp_window = self._make_spin(
            "Temperature window", "samples",
            cfg.temp_window, 5, 60,
        )
        layout.addLayout(self._temp_window)

        self._write_delta = self._make_double_spin(
            "Write min delta", "%",
            cfg.write_min_delta_pct, 0.5, 20.0, 0.5,
        )
        layout.addLayout(self._write_delta)

        self._ramp_up = self._make_double_spin(
            "Ramp up rate", "% / tick",
            cfg.ramp_up_pct, 1.0, 100.0, 1.0,
        )
        layout.addLayout(self._ramp_up)

        self._ramp_down = self._make_double_spin(
            "Ramp down rate", "% / tick",
            cfg.ramp_down_pct, 1.0, 100.0, 1.0,
        )
        layout.addLayout(self._ramp_down)

        layout.addStretch()

        # ── Apply button ──
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setFixedWidth(120)
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent_blue']};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #4db8f2;
            }}
            QPushButton:pressed {{
                background-color: #2a9edf;
            }}
        """)
        self._apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self._apply_btn)
        layout.addLayout(apply_row)

    def _make_spin(self, label: str, suffix: str, value: int,
                   vmin: int, vmax: int) -> QHBoxLayout:
        return make_spin(label, suffix, value, vmin, vmax)

    def _make_double_spin(self, label: str, suffix: str, value: float,
                          vmin: float, vmax: float, step: float) -> QHBoxLayout:
        return make_double_spin(label, suffix, value, vmin, vmax, step)

    def _on_apply(self):
        cfg = api.get_fan_config()
        cfg.ramp_down_delay = float(self._ramp_delay._spin.value())
        cfg.temp_window = self._temp_window._spin.value()
        cfg.write_min_delta_pct = self._write_delta._spin.value()
        cfg.ramp_up_pct = self._ramp_up._spin.value()
        cfg.ramp_down_pct = self._ramp_down._spin.value()

        from hp_helper.backend import fan_config
        fan_config.save_all(cfg)
