"""Program settings page."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QMessageBox,
)
from PySide6.QtGui import QColor, QPalette, QDesktopServices
from PySide6.QtCore import Qt, QUrl

from victus_hub.app.theme import COLORS
from victus_hub.backend import fan_config
from victus_hub.backend.diagnostics import write_diagnostics_report
from victus_hub.features.keyboard.shortcut import (
    KeybindSettings,
    keybind_from_event,
    keybind_label,
    read_keybind_settings,
    write_keybind_settings,
)


def make_settings_card() -> tuple[QWidget, QVBoxLayout]:
    """Rounded surface card used on Settings and Power."""
    card = QWidget()
    card.setProperty("settingsCard", True)
    card.setStyleSheet(f"""
        QWidget[settingsCard="true"] {{
            background-color: {COLORS['surface']};
            border: 1px solid {COLORS['border']};
            border-radius: 14px;
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)
    return card, layout


def make_card_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;"
        f"background: transparent;"
    )
    return lbl


def _style_native_spinbox(spin: QSpinBox | QDoubleSpinBox) -> None:
    palette = spin.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface_raised"]))
    spin.setPalette(palette)
    spin.setFixedHeight(40)


def make_spin(label: str, suffix: str, value: int,
              vmin: int, vmax: int, compact: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})")
    lbl.setStyleSheet(
        f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
    )
    row.addWidget(lbl)
    if not compact:
        row.addStretch()
    spin = QSpinBox()
    spin.setRange(vmin, vmax)
    spin.setValue(value)
    spin.setFixedWidth(90)
    _style_native_spinbox(spin)
    row._spin = spin
    row.addWidget(spin)
    return row


def make_double_spin(label: str, suffix: str, value: float,
                     vmin: float, vmax: float, step: float) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})")
    lbl.setStyleSheet(
        f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
    )
    row.addWidget(lbl)
    row.addStretch()
    spin = QDoubleSpinBox()
    spin.setRange(vmin, vmax)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    spin.setFixedWidth(90)
    _style_native_spinbox(spin)
    row._spin = spin
    row.addWidget(spin)
    return row


class SettingsPage(QWidget):
    """Settings tab: fan constants, program shortcut, diagnostics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        fan_panel, fan_layout = make_settings_card()

        cfg = fan_config.load()
        fan_layout.addWidget(make_card_title("Fan Control"))
        self._min_fan_change = make_double_spin(
            "Minimum fan change", "%",
            cfg.min_fan_change_pct, 0.0, 20.0, 0.5,
        )
        self._min_fan_change._spin.valueChanged.connect(self._save_min_fan_change)
        fan_layout.addLayout(self._min_fan_change)
        layout.addWidget(fan_panel)

        # ── Program Shortcut ──
        self._shortcut_ctrl = None  # set by MainWindow via set_shortcut_controller
        self._kb = read_keybind_settings()
        if self._kb.key != 0 and not self._kb.enabled:
            self._kb = KeybindSettings(enabled=True, mods=self._kb.mods, key=self._kb.key)
            write_keybind_settings(self._kb)

        shortcut_panel, shortcut_layout = make_settings_card()
        shortcut_label = make_card_title("Program Shortcut")

        shortcut_row = QHBoxLayout()
        shortcut_row.setSpacing(16)
        shortcut_row.addWidget(shortcut_label)
        shortcut_row.addStretch()

        # Current keybind display + capture/clear buttons
        kb_row = QHBoxLayout()
        kb_row.setSpacing(8)
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
        self._kb_set_btn.setFixedHeight(34)
        self._kb_set_btn.setStyleSheet(self._shortcut_btn_style(False))
        self._kb_set_btn.clicked.connect(self._on_set_shortcut)
        kb_row.addWidget(self._kb_set_btn)

        self._kb_clear_btn = QPushButton("Clear")
        self._kb_clear_btn.setFixedWidth(80)
        self._kb_clear_btn.setFixedHeight(34)
        self._kb_clear_btn.setEnabled(self._kb.key != 0)
        self._kb_clear_btn.clicked.connect(self._on_clear_shortcut)
        kb_row.addWidget(self._kb_clear_btn)

        shortcut_row.addLayout(kb_row)
        shortcut_layout.addLayout(shortcut_row)

        layout.addWidget(shortcut_panel)

        diag_panel, diag_layout = make_settings_card()
        diag_layout.addWidget(make_card_title("Diagnostics"))
        diag_row = QHBoxLayout()
        diag_row.setSpacing(12)
        diag_hint = QLabel(
            "Save a markdown report with system info, hardware capabilities, "
            "and this session's terminal log to your Documents folder."
        )
        diag_hint.setWordWrap(True)
        diag_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
        )
        diag_row.addWidget(diag_hint, 1)
        self._diag_btn = QPushButton("Generate report")
        self._diag_btn.setFixedHeight(34)
        self._diag_btn.setStyleSheet(self._shortcut_btn_style(False))
        self._diag_btn.clicked.connect(self._on_generate_diagnostics)
        diag_row.addWidget(self._diag_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        diag_layout.addLayout(diag_row)
        layout.addWidget(diag_panel)

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
                padding: 8px 12px;
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
        self._kb = KeybindSettings(enabled=True, mods=self._kb.mods, key=self._kb.key)
        write_keybind_settings(self._kb)
        self._finish_capture()
        self._kb_clear_btn.setEnabled(True)
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.reload_settings()

    def _on_clear_shortcut(self):
        self._kb = KeybindSettings(enabled=False, mods=(), key=0)
        write_keybind_settings(self._kb)
        self._finish_capture()
        self._kb_clear_btn.setEnabled(False)
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.cancel_capture()
            self._shortcut_ctrl.reload_settings()

    def _on_generate_diagnostics(self) -> None:
        try:
            path = write_diagnostics_report()
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Diagnostics",
                f"Could not write the report:\n{exc}",
            )
            return
        box = QMessageBox(self)
        box.setWindowTitle("Diagnostics")
        box.setText(f"Report saved to:\n{path}")
        open_btn = box.addButton("Open", QMessageBox.AcceptRole)
        box.addButton("OK", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
