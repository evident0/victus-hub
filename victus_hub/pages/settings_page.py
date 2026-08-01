"""Program settings page."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QCheckBox,
)
from PySide6.QtCore import Qt

from victus_hub.app.theme import COLORS
from victus_hub.backend import fan_config
from victus_hub.features.keyboard.shortcut import (
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
                     vmin: float, vmax: float, step: float) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})")
    lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
    row.addWidget(lbl)
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
    """Settings tab for the program shortcut."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        cfg = fan_config.load()
        fan_label = QLabel("Fan Control")
        fan_label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;"
        )
        layout.addWidget(fan_label)
        self._min_fan_change = make_double_spin(
            "Minimum fan change", "%",
            cfg.min_fan_change_pct, 0.0, 20.0, 0.5,
        )
        self._min_fan_change._spin.valueChanged.connect(self._save_min_fan_change)
        layout.addLayout(self._min_fan_change)

        # ── Program Shortcut ──
        self._shortcut_ctrl = None  # set by MainWindow via set_shortcut_controller
        self._kb = read_keybind_settings()

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

        layout.addStretch()

    # ── Program Shortcut ──

    def _save_min_fan_change(self, value: float) -> None:
        cfg = fan_config.load()
        cfg.min_fan_change_pct = max(float(value), 0.0)
        fan_config.save_all(cfg)

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
