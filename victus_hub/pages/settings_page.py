"""Program settings page — Ohman rows, hairlines, footer links."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QMessageBox, QScrollArea, QSizePolicy,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QUrl

from victus_hub.app.theme import COLORS, mono_font, ui_font
from victus_hub.backend import fan_config
from victus_hub.backend.diagnostics import write_diagnostics_report
from victus_hub.backend.hardware import board_title
from victus_hub.features.keyboard.shortcut import (
    KeybindSettings,
    keybind_from_event,
    keybind_label,
    read_keybind_settings,
    write_keybind_settings,
)
from victus_hub.widgets.chrome import PageHead, SettingsRow, hairline
from victus_hub.widgets.toggle_switch import ToggleSwitch


def make_settings_card() -> tuple[QWidget, QVBoxLayout]:
    """Kept for Power page: a transparent stack, no card chrome."""
    card = QWidget()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return card, layout


def make_card_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(ui_font(14, 600))
    lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
    return lbl


def _style_action_btn(btn: QPushButton) -> None:
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(34)
    btn.setStyleSheet(
        f"QPushButton {{"
        f" background-color: {COLORS['pill']}; color: {COLORS['text']};"
        f" border: 1px solid {COLORS['edge']}; border-radius: 8px;"
        f" padding: 0 12px; font-size: 13px;"
        f"}}"
        f"QPushButton:hover {{ background-color: {COLORS['edge']}; }}"
        f"QPushButton:disabled {{"
        f" color: {COLORS['foot']}; background-color: {COLORS['sunken']};"
        f"}}"
    )


def _style_native_spinbox(spin: QSpinBox | QDoubleSpinBox) -> None:
    spin.setObjectName("embedSpin")
    spin.setAttribute(Qt.WA_StyledBackground, True)
    spin.setFixedHeight(34)
    spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


def _spin_well(spin: QSpinBox | QDoubleSpinBox) -> QWidget:
    """Sunken chip behind a spinbox so the field reads darker than the page."""
    well = QWidget()
    well.setAttribute(Qt.WA_StyledBackground, True)
    well.setFixedHeight(34)
    well.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    well.setStyleSheet(
        f"background-color: {COLORS['sunken']};"
        f" border: 1px solid {COLORS['edge']};"
        f" border-radius: 8px;"
    )
    lay = QHBoxLayout(well)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(spin)
    return well


def make_spin(label: str, suffix: str, value: int,
              vmin: int, vmax: int, compact: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(f"{label} ({suffix})" if not compact else suffix)
    lbl.setFont(ui_font(12))
    lbl.setStyleSheet(
        f"color: {COLORS['sub']}; background: transparent;"
    )
    row.addWidget(lbl)
    if not compact:
        row.addStretch()
    spin = QSpinBox()
    spin.setRange(vmin, vmax)
    spin.setValue(value)
    _style_native_spinbox(spin)
    row._spin = spin
    row.addWidget(_spin_well(spin))
    return row


def make_double_spin(label: str, suffix: str, value: float,
                     vmin: float, vmax: float, step: float) -> QHBoxLayout:
    row = QHBoxLayout()
    if label:
        lbl = QLabel(f"{label} ({suffix})" if suffix else label)
        lbl.setFont(ui_font(12))
        lbl.setStyleSheet(
            f"color: {COLORS['sub']}; background: transparent;"
        )
        row.addWidget(lbl)
        row.addStretch()
    spin = QDoubleSpinBox()
    spin.setRange(vmin, vmax)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(f" {suffix}" if not suffix.startswith(" ") else suffix)
    _style_native_spinbox(spin)
    row._spin = spin
    row.addWidget(_spin_well(spin))
    return row


class SettingsPage(QWidget):
    """Settings tab: fan constants, program shortcut, diagnostics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head_wrap = QWidget()
        head_l = QVBoxLayout(head_wrap)
        head_l.setContentsMargins(24, 24, 24, 16)
        self._head = PageHead("Settings")
        try:
            self._head.set_status(board_title())
        except Exception:
            self._head.set_status("")
        head_l.addWidget(self._head)
        outer.addWidget(head_wrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background-color: {COLORS['bg']}; border: none;")
        body = QWidget()
        body.setStyleSheet(f"background-color: {COLORS['bg']};")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 4, 16, 8)
        layout.setSpacing(0)

        self._shortcut_ctrl = None
        self._kb = read_keybind_settings()
        if self._kb.key != 0 and not self._kb.enabled:
            self._kb = KeybindSettings(enabled=True, mods=self._kb.mods, key=self._kb.key)
            write_keybind_settings(self._kb)

        kb_ctrl = QWidget()
        kb_l = QHBoxLayout(kb_ctrl)
        kb_l.setContentsMargins(0, 0, 0, 0)
        kb_l.setSpacing(8)
        self._kb_value = QLabel(self._shortcut_text(self._kb))
        self._kb_value.setFont(mono_font(12, 600))
        self._kb_value.setStyleSheet(
            f"color: {COLORS['text']}; background-color: {COLORS['sunken']}; "
            f"border-radius: 8px; padding: 7px 10px;"
        )
        self._kb_value.setMinimumWidth(120)
        kb_l.addWidget(self._kb_value)
        self._kb_set_btn = QPushButton("Set")
        _style_action_btn(self._kb_set_btn)
        self._kb_set_btn.clicked.connect(self._on_set_shortcut)
        kb_l.addWidget(self._kb_set_btn)
        self._kb_clear_btn = QPushButton("Clear")
        _style_action_btn(self._kb_clear_btn)
        self._kb_clear_btn.setEnabled(self._kb.key != 0)
        self._kb_clear_btn.clicked.connect(self._on_clear_shortcut)
        kb_l.addWidget(self._kb_clear_btn)
        layout.addWidget(SettingsRow(
            "Program shortcut",
            "Raises the panel from anywhere",
            kb_ctrl,
        ))

        layout.addWidget(hairline())

        cfg = fan_config.load()
        fan_ctrl = QWidget()
        fan_l = QHBoxLayout(fan_ctrl)
        fan_l.setContentsMargins(0, 0, 0, 0)
        self._min_fan_change = make_double_spin(
            "", "%",
            cfg.min_fan_change_pct, 0.0, 20.0, 0.5,
        )
        self._min_fan_change._spin.valueChanged.connect(self._save_min_fan_change)
        fan_l.addLayout(self._min_fan_change)
        layout.addWidget(SettingsRow(
            "Minimum fan change",
            "Ignore smaller PWM steps",
            fan_ctrl,
        ))

        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        foot = QWidget()
        foot.setStyleSheet(
            f"background-color: {COLORS['bg']}; border-top: 1px solid {COLORS['line']};"
        )
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 16, 24, 16)
        fl.setSpacing(22)
        self._diag_btn = QPushButton("Diagnostics")
        self._diag_btn.setObjectName("linkBtn")
        self._diag_btn.setFlat(True)
        self._diag_btn.setCursor(Qt.PointingHandCursor)
        self._diag_btn.clicked.connect(self._on_generate_diagnostics)
        self._diag_btn.setStyleSheet(
            f"color: {COLORS['seg_text']}; background: transparent; border: none;"
        )
        fl.addWidget(self._diag_btn)
        fl.addStretch()
        outer.addWidget(foot)

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

    def _on_set_shortcut(self):
        if self._shortcut_ctrl is None:
            return
        if self._shortcut_ctrl.is_capturing():
            self._cancel_capture()
            return
        self._shortcut_ctrl.start_capture()
        self._kb_set_btn.setText("Press a key…")
        self._kb_value.setText("(waiting)")
        self._kb_value.setStyleSheet(
            f"color: {COLORS['sub']}; background-color: {COLORS['sunken']}; "
            f"border-radius: 8px; padding: 7px 10px;"
        )

    def _cancel_capture(self):
        if self._shortcut_ctrl is not None:
            self._shortcut_ctrl.cancel_capture()
        self._finish_capture()

    def _finish_capture(self):
        self._kb_set_btn.setText("Set")
        self._kb_value.setText(self._shortcut_text(self._kb))
        self._kb_value.setStyleSheet(
            f"color: {COLORS['text']}; background-color: {COLORS['sunken']}; "
            f"border-radius: 8px; padding: 7px 10px;"
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
