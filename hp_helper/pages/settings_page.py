"""Settings page — fan control constants and the program shortcut."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QCheckBox, QFrame,
)
from PySide6.QtCore import Qt

from hp_helper import api
from hp_helper.theme import COLORS
from hp_helper.backend.types import FanConfig
from hp_helper.keyboard_shortcut import (
    KeybindSettings,
    keybind_from_event,
    keybind_label,
    read_keybind_settings,
    write_keybind_settings,
)


def make_spin(label: str, suffix: str, value: int,
              vmin: int, vmax: int, compact: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})")
    lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
    row.addWidget(lbl)
    if not compact:
        row.addStretch()
    spin = QSpinBox()
    spin.setRange(vmin, vmax)
    spin.setValue(value)
    spin.setFixedWidth(90)
    row._spin = spin
    row.addWidget(spin)
    return row

def make_double_spin(label: str, suffix: str, value: float,
                     vmin: float, vmax: float, step: float, compact: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})")
    lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
    row.addWidget(lbl)
    if not compact:
        row.addStretch()
    spin = QDoubleSpinBox()
    spin.setRange(vmin, vmax)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    spin.setFixedWidth(90)
    row._spin = spin
    row.addWidget(spin)
    return row

class SettingsPage(QWidget):
    """Settings tab for fan-control tuning constants and the program shortcut."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        cfg = api.get_fan_config()

        # ── Fan control constants ──
        constants_label = QLabel("Fan control constants")
        constants_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;")
        layout.addWidget(constants_label)

        self._ramp_delay = make_spin(
            "Ramp-down delay", "s",
            int(cfg.ramp_down_delay), 0, 120,
        )
        layout.addLayout(self._ramp_delay)
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

        # Apply (fan control constants only)
        apply_row = QHBoxLayout()
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
        apply_row.addStretch()
        layout.addLayout(apply_row)

        # ── Program Shortcut ──
        self._shortcut_ctrl = None  # set by MainWindow via set_shortcut_controller
        self._kb = read_keybind_settings()

        layout.addSpacing(16)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(sep)
        layout.addSpacing(12)

        shortcut_label = QLabel("Program Shortcut")
        shortcut_label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;"
        )
        layout.addWidget(shortcut_label)

        hint = QLabel("Set a key or key combination to unhide the program from the tray.\n"
                      "Single keys like the OMEN key are supported.")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Current keybind display + capture/clear buttons
        kb_row = QHBoxLayout()
        self._kb_value = QLabel(self._shortcut_text(self._kb))
        self._kb_value.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; font-weight: bold;"
            f"background-color: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
            f"border-radius: 4px; padding: 6px 10px;"
        )
        self._kb_value.setMinimumWidth(120)
        kb_row.addWidget(self._kb_value)

        self._kb_set_btn = QPushButton("Set shortcut")
        self._kb_set_btn.setFixedWidth(120)
        self._kb_set_btn.setStyleSheet(self._shortcut_btn_style(False))
        self._kb_set_btn.clicked.connect(self._on_set_shortcut)
        kb_row.addWidget(self._kb_set_btn)

        self._kb_clear_btn = QPushButton("Clear")
        self._kb_clear_btn.setFixedWidth(80)
        self._kb_clear_btn.setEnabled(self._kb.key != 0)
        self._kb_clear_btn.clicked.connect(self._on_clear_shortcut)
        kb_row.addWidget(self._kb_clear_btn)

        kb_row.addStretch()
        layout.addLayout(kb_row)

        # Enable toggle
        self._kb_enable = QCheckBox("Enabled")
        self._kb_enable.setChecked(self._kb.enabled)
        self._kb_enable.setEnabled(self._kb.key != 0)
        self._kb_enable.toggled.connect(self._on_shortcut_enabled)
        layout.addWidget(self._kb_enable)

        # ── GPU P0 min RPM floor ──
        layout.addSpacing(16)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(sep2)
        layout.addSpacing(12)

        p0_label = QLabel("GPU P0 fan floor")
        p0_label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;"
        )
        layout.addWidget(p0_label)

        p0_hint = QLabel(
            "Engages after ~6s continuous P0; holds ~25s after P0 ends. "
            "Custom fan mode only. The override ramps the fan up to the "
            "minimum % and blocks the curve below it until ~25s of non-P0."
        )
        p0_hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        p0_hint.setWordWrap(True)
        layout.addWidget(p0_hint)

        self._p0_enable = QCheckBox(
            "Override fan curve minimums when gpu perf is P0"
        )
        self._p0_enable.setChecked(cfg.p0_min_pct_enabled)
        self._p0_enable.toggled.connect(self._on_p0_changed)
        layout.addWidget(self._p0_enable)

        self._p0_min_pct = make_spin(
            "Minimum fan speed", "%",
            cfg.p0_min_pct, 0, 100,
        )
        self._p0_min_pct._spin.setFixedWidth(90)
        self._p0_min_pct._spin.valueChanged.connect(self._on_p0_changed)
        layout.addLayout(self._p0_min_pct)

        layout.addStretch()

    def _make_spin(self, label: str, suffix: str, value: int,
                   vmin: int, vmax: int, compact: bool = False) -> QHBoxLayout:
        return make_spin(label, suffix, value, vmin, vmax, compact=compact)

    def _make_double_spin(self, label: str, suffix: str, value: float,
                          vmin: float, vmax: float, step: float, compact: bool = False) -> QHBoxLayout:
        return make_double_spin(label, suffix, value, vmin, vmax, step, compact=compact)

    def _on_apply(self):
        """Persist fan control constants only."""
        cfg = api.get_fan_config()
        cfg.ramp_down_delay = float(self._ramp_delay._spin.value())
        cfg.temp_window = self._temp_window._spin.value()
        cfg.write_min_delta_pct = self._write_delta._spin.value()
        cfg.ramp_up_pct = self._ramp_up._spin.value()
        cfg.ramp_down_pct = self._ramp_down._spin.value()

        from hp_helper.backend import fan_config
        fan_config.save_all(cfg)

    def _on_p0_changed(self, *_args):
        """Persist P0 floor settings immediately (not gated by Apply)."""
        cfg = api.get_fan_config()
        cfg.p0_min_pct_enabled = self._p0_enable.isChecked()
        cfg.p0_min_pct = self._p0_min_pct._spin.value()

        from hp_helper.backend import fan_config
        fan_config.save_all(cfg)


    # ── Program Shortcut ──

    def set_shortcut_controller(self, ctrl) -> None:
        """Wire the shared ShortcutController (owned by MainWindow)."""
        self._shortcut_ctrl = ctrl
        ctrl.captured.connect(self._on_shortcut_captured)

    def _shortcut_text(self, s: KeybindSettings) -> str:
        return keybind_label(s.mods, s.key)

    def _shortcut_btn_style(self, capturing: bool) -> str:
        bg = COLORS['accent_blue'] if not capturing else COLORS['accent_red']
        return f"""
            QPushButton {{
                background-color: {bg};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #4db8f2;
            }}
            QPushButton:disabled {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['text_secondary']};
            }}
        """

    def _on_set_shortcut(self):
        if self._shortcut_ctrl is None:
            return
        if self._shortcut_ctrl.is_capturing():
            self._cancel_capture()
            return
        self._shortcut_ctrl.start_capture()
        self._kb_set_btn.setText("Press a key\u2026")
        self._kb_set_btn.setStyleSheet(self._shortcut_btn_style(True))
        self._kb_value.setText("(waiting for keypress)")
        self._kb_value.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px;"
            f"background-color: {COLORS['surface']}; border: 1px solid {COLORS['border_focus']};"
            f"border-radius: 4px; padding: 6px 10px;"
        )

    def _cancel_capture(self):
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.cancel_capture()
        self._finish_capture()

    def _finish_capture(self):
        self._kb_set_btn.setText("Set shortcut")
        self._kb_set_btn.setStyleSheet(self._shortcut_btn_style(False))
        self._kb_value.setText(self._shortcut_text(self._kb))
        self._kb_value.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; font-weight: bold;"
            f"background-color: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
            f"border-radius: 4px; padding: 6px 10px;"
        )

    def _on_shortcut_captured(self, mods, key: int):
        self._kb = keybind_from_event(mods, key)
        write_keybind_settings(self._kb)
        self._finish_capture()
        self._kb_clear_btn.setEnabled(True)
        self._kb_enable.setEnabled(True)
        self._kb_enable.setChecked(True)
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.reload_settings()

    def _on_clear_shortcut(self):
        self._kb = KeybindSettings(enabled=self._kb.enabled, mods=(), key=0)
        write_keybind_settings(self._kb)
        self._finish_capture()
        self._kb_clear_btn.setEnabled(False)
        self._kb_enable.setEnabled(False)
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.cancel_capture()
            self._shortcut_ctrl.reload_settings()

    def _on_shortcut_enabled(self, checked: bool):
        self._kb = KeybindSettings(
            enabled=checked, mods=self._kb.mods, key=self._kb.key,
        )
        write_keybind_settings(self._kb)
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.reload_settings()
