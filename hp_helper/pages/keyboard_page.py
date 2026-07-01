"""Keyboard page with lighting controls and visual keyboard preview."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QComboBox, QPushButton, QSlider, QColorDialog,
)
from PySide6.QtCore import Qt, Signal, QRectF, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont

from hp_helper.widgets.section_title import SectionTitle
from hp_helper.theme import COLORS
from hp_helper.keyboard_lighting import (
    LightingEffect, LIGHTING_EFFECTS, LightingSettings,
    read_lighting_settings, write_lighting_settings, hex_to_rgb,
    lighting_frame, normalize_lighting_settings,
)


KEYBOARD_ROWS: list[list[float]] = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2],
    [1.5, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.5],
    [1.75, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2.25],
    [2.25, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2.75],
    [1.25, 1.25, 1.25, 1.25, 6.25, 1.25, 1.25, 1.25],
]


class KeyboardVisual(QWidget):
    """Custom paint widget showing a visual keyboard with lighting effects."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key_color = QColor("#2a2a2a")
        self._effect = "static"
        self._enabled = False
        self._breathing_opacity = 1.0
        self._cycle_hue = 0.0
        self._strobe_on = True
        self.setMinimumHeight(140)

    def set_key_state(self, enabled: bool, effect: str, color: QColor):
        self._enabled = enabled
        self._effect = effect
        if enabled and effect != "color-cycle":
            self._key_color = color
        else:
            self._key_color = color if enabled else QColor("#2a2a2a")

    def set_breathing_frame(self, opacity: float):
        self._breathing_opacity = opacity
        self.update()

    def set_cycle_hue(self, hue: float):
        self._cycle_hue = hue
        self.update()

    def set_strobe_on(self, on: bool):
        self._strobe_on = on
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        rows = len(KEYBOARD_ROWS)
        row_h = (h - (rows - 1) * 2) / rows
        y = 0
        max_cols = max(len(r) for r in KEYBOARD_ROWS)
        unit_w = (w - (max_cols - 1) * 2) / sum(KEYBOARD_ROWS[0])  # approx

        # Background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#121212"))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)

        for row in KEYBOARD_ROWS:
            x = 0
            total_flex = sum(row)
            row_width = w - (len(row) - 1) * 2
            flex_unit = row_width / total_flex if total_flex > 0 else 10

            for flex in row:
                key_w = flex * flex_unit
                key_rect = QRectF(x + 1, y + 1, key_w, row_h)

                if self._enabled:
                    if self._effect == "color-cycle":
                        c = self._hue_color(self._cycle_hue)
                    elif self._effect == "breathing":
                        c = QColor(
                            int(self._key_color.red() * self._breathing_opacity),
                            int(self._key_color.green() * self._breathing_opacity),
                            int(self._key_color.blue() * self._breathing_opacity),
                        )
                    elif self._effect == "strobe":
                        c = self._key_color if self._strobe_on else QColor("#1a1a1a")
                    else:
                        c = self._key_color
                else:
                    c = QColor("#2a2a2a")

                painter.setBrush(c)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(key_rect, 3, 3)

                x += key_w + 2
            y += row_h + 2

        painter.end()

    @staticmethod
    def _hue_color(hue: float) -> QColor:
        """Convert hue 0-1 to QColor (full sat, full val)."""
        from hp_helper.keyboard_lighting import hsv_to_rgb
        rgb = hsv_to_rgb(hue)
        return QColor(rgb.red, rgb.green, rgb.blue)


class KeyboardPage(QWidget):
    """Keyboard lighting tab with controls and visual preview."""

    enabled_changed = Signal(bool)
    effect_changed = Signal(str)
    color_changed = Signal(str)
    speed_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Load settings
        s = read_lighting_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Title
        self._status_label = QLabel("Keyboard RGB ready")
        self._status_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        title = SectionTitle("\u25A4", "Keyboard Lighting")
        layout.addWidget(title)

        # Visual keyboard
        self._visual = KeyboardVisual()
        self._visual.set_key_state(s.enabled, s.effect, QColor(s.color))
        layout.addWidget(self._visual, 1)

        # Controls grid
        controls = QWidget()
        controls.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(controls)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(12)

        # Enable
        self._enable_check = QCheckBox("RGB enabled")
        self._enable_check.setChecked(s.enabled)
        self._enable_check.toggled.connect(self._on_enabled_changed)
        ctrl_layout.addWidget(self._enable_check)

        # Effect
        effect_label = QLabel("Effect")
        effect_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        ctrl_layout.addWidget(effect_label)

        self._effect_combo = QComboBox()
        for opt in LIGHTING_EFFECTS:
            self._effect_combo.addItem(opt["label"], opt["value"])
        idx = next((i for i in range(self._effect_combo.count())
                    if self._effect_combo.itemData(i) == s.effect), 0)
        self._effect_combo.setCurrentIndex(idx)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        ctrl_layout.addWidget(self._effect_combo)

        # Color
        color_label = QLabel("Color")
        color_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        ctrl_layout.addWidget(color_label)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(34, 28)
        self._color_btn.setStyleSheet(
            f"background-color: {s.color}; border: 1px solid {COLORS['border']}; border-radius: 4px;"
        )
        self._color_btn.clicked.connect(self._pick_color)
        ctrl_layout.addWidget(self._color_btn)

        # Speed
        speed_label = QLabel("Speed")
        speed_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        ctrl_layout.addWidget(speed_label)

        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(1, 10)
        self._speed_slider.setValue(s.speed)
        self._speed_slider.setFixedWidth(100)
        self._speed_slider.setEnabled(s.effect != "static")
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        ctrl_layout.addWidget(self._speed_slider)

        ctrl_layout.addStretch()
        layout.addWidget(controls)

        # Store state
        self._settings = s

    def set_status(self, text: str):
        self._status_label.setText(text)

    def _on_enabled_changed(self, checked: bool):
        self._settings.enabled = checked
        write_lighting_settings(self._settings)
        self._visual.set_key_state(checked, self._settings.effect, QColor(self._settings.color))
        self.enabled_changed.emit(checked)

    def _on_effect_changed(self, index: int):
        value = self._effect_combo.itemData(index)
        self._settings.effect = value
        self._speed_slider.setEnabled(value != "static")
        write_lighting_settings(self._settings)
        self._visual.set_key_state(self._settings.enabled, value, QColor(self._settings.color))
        self.effect_changed.emit(value)

    def _pick_color(self):
        current = QColor(self._settings.color)
        color = QColorDialog.getColor(current, self, "Keyboard Color")
        if color.isValid():
            hex_str = color.name()
            self._settings.color = hex_str
            self._color_btn.setStyleSheet(
                f"background-color: {hex_str}; border: 1px solid {COLORS['border']}; border-radius: 4px;"
            )
            write_lighting_settings(self._settings)
            self._visual.set_key_state(self._settings.enabled, self._settings.effect, color)
            self.color_changed.emit(hex_str)

    def _on_speed_changed(self, value: int):
        self._settings.speed = value
        write_lighting_settings(self._settings)
        self.speed_changed.emit(value)

    # ── Animation frame update ──

    def apply_frame(self, color_rgb):
        """Update the visual keyboard from a computed frame color."""
        r, g, b = color_rgb.red, color_rgb.green, color_rgb.blue
        qc = QColor(r, g, b)
        if self._settings.effect == "breathing":
            max_val = max(r, g, b)
            opacity = max_val / 255.0 if max_val > 0 else 0.0
            self._visual.set_breathing_frame(opacity)
        elif self._settings.effect == "color-cycle":
            # Extract approximate hue
            self._visual.set_cycle_hue(self._approx_hue(r, g, b))
        elif self._settings.effect == "strobe":
            self._visual.set_strobe_on(max(r, g, b) > 128)
        self._visual.set_key_state(self._settings.enabled, self._settings.effect, qc)

    @staticmethod
    def _approx_hue(r: int, g: int, b: int) -> float:
        import math
        rn, gn, bn = r / 255.0, g / 255.0, b / 255.0
        mx = max(rn, gn, bn)
        mn = min(rn, gn, bn)
        if mx == mn:
            return 0.0
        d = mx - mn
        if mx == rn:
            h = ((gn - bn) / d) % 6
        elif mx == gn:
            h = (bn - rn) / d + 2
        else:
            h = (rn - gn) / d + 4
        return (h / 6.0) % 1.0
