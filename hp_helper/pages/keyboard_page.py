"""Keyboard page with lighting controls and visual keyboard preview."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QComboBox, QPushButton, QSlider, QSpinBox, QColorDialog,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QLinearGradient

from hp_helper.widgets.section_title import SectionTitle
from hp_helper.theme import COLORS
from hp_helper.keyboard_lighting import (
    LIGHTING_EFFECTS,
    read_lighting_settings, write_lighting_settings, hsv_to_rgb,
)

# ── Keyboard layout ──
# Each key is (label, width_units, gap_before_units).  The main rows all
# total 15u; the function row is ~13.3u and centered above them.

Key = tuple[str, float, float]  # (label, width, gap_before)

_KEYBOARD: list[list[Key]] = [
    # Function row — gaps after F4 and F8
    [("esc", 1.0, 0.0), ("f1", 1.0, 0.25), ("f2", 1.0, 0.0), ("f3", 1.0, 0.0), ("f4", 1.0, 0.0),
     ("f5", 1.0, 0.5), ("f6", 1.0, 0.0), ("f7", 1.0, 0.0), ("f8", 1.0, 0.0),
     ("f9", 1.0, 0.5), ("f10", 1.0, 0.0), ("f11", 1.0, 0.0), ("f12", 1.0, 0.0)],
    # Number row
    [("`", 1.0, 0.0), ("1", 1.0, 0.0), ("2", 1.0, 0.0), ("3", 1.0, 0.0), ("4", 1.0, 0.0),
     ("5", 1.0, 0.0), ("6", 1.0, 0.0), ("7", 1.0, 0.0), ("8", 1.0, 0.0), ("9", 1.0, 0.0),
     ("0", 1.0, 0.0), ("-", 1.0, 0.0), ("=", 1.0, 0.0), ("\u232B", 2.0, 0.0)],  # ⌫
    # QWERTY row
    [("tab", 1.5, 0.0), ("q", 1.0, 0.0), ("w", 1.0, 0.0), ("e", 1.0, 0.0), ("r", 1.0, 0.0),
     ("t", 1.0, 0.0), ("y", 1.0, 0.0), ("u", 1.0, 0.0), ("i", 1.0, 0.0), ("o", 1.0, 0.0),
     ("p", 1.0, 0.0), ("[", 1.0, 0.0), ("]", 1.0, 0.0), ("\\", 1.5, 0.0)],
    # Home row
    [("caps", 1.75, 0.0), ("a", 1.0, 0.0), ("s", 1.0, 0.0), ("d", 1.0, 0.0), ("f", 1.0, 0.0),
     ("g", 1.0, 0.0), ("h", 1.0, 0.0), ("j", 1.0, 0.0), ("k", 1.0, 0.0), ("l", 1.0, 0.0),
     (";", 1.0, 0.0), ("'", 1.0, 0.0), ("\u23CE", 2.25, 0.0)],  # ⏎
    # Shift row
    [("\u21E7", 2.25, 0.0), ("z", 1.0, 0.0), ("x", 1.0, 0.0), ("c", 1.0, 0.0), ("v", 1.0, 0.0),
     ("b", 1.0, 0.0), ("n", 1.0, 0.0), ("m", 1.0, 0.0), (",", 1.0, 0.0), (".", 1.0, 0.0),
     ("/", 1.0, 0.0), ("\u21E7", 2.75, 0.0)],
    # Bottom row
    [("ctrl", 1.25, 0.0), ("fn", 1.25, 0.0), ("alt", 1.25, 0.0), ("", 4.25, 0.0),
     ("alt", 1.25, 0.0), ("ctrl", 1.25, 0.0),
     ("\u2190", 1.0, 0.5), ("\u2193", 1.0, 0.0), ("\u2191", 1.0, 0.0), ("\u2192", 1.0, 0.0)],
]

_MAIN_ROW_WIDTH = 15.0  # units for rows 1-5
_GAP_PX = 3.0           # gap between keys in pixels
_KEY_RADIUS = 4.0       # rounded-corner radius
_CHASSIS_PAD = 12.0     # padding inside the chassis to the key block
_CHASSIS_MARGIN = 10.0  # margin around chassis within the widget


class KeyboardVisual(QWidget):
    """Visual keyboard with per-key lighting effects.

    Supports static, breathing (global opacity), color-cycle (rainbow wave
    across keys), and strobe effects.  Each key is painted with its label
    and a subtle glow when lit.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key_color = QColor("#2a2a2a")
        self._effect: str = "static"
        self._enabled = False
        self._breathing_opacity = 1.0
        self._cycle_hue = 0.0
        self._strobe_on = True
        self.setMinimumHeight(240)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           self.sizePolicy().verticalPolicy())

    def set_key_state(self, enabled: bool, effect: str, color: QColor):
        self._enabled = enabled
        self._effect = effect
        self._key_color = color if enabled else QColor("#2a2a2a")
        self.update()

    def set_breathing_frame(self, opacity: float):
        self._breathing_opacity = opacity
        self.update()

    def set_cycle_hue(self, hue: float):
        self._cycle_hue = hue
        self.update()

    def set_strobe_on(self, on: bool):
        self._strobe_on = on
        self.update()

    # ── Key color computation ──

    def _key_color_at(self, key_x: float, key_y: float) -> QColor:
        """Color for a single key at flex-space position (key_x, key_y)."""
        if not self._enabled:
            return QColor("#2a2a2a")

        if self._effect == "color-cycle":
            # Rainbow wave: hue shifts with key position across board
            hue = (self._cycle_hue + key_x * 0.025 + key_y * 0.06) % 1.0
            rgb = hsv_to_rgb(hue)
            return QColor(rgb.red, rgb.green, rgb.blue)
        elif self._effect == "breathing":
            op = max(0.0, min(1.0, self._breathing_opacity))
            return QColor(
                int(self._key_color.red() * op),
                int(self._key_color.green() * op),
                int(self._key_color.blue() * op),
            )
        elif self._effect == "strobe":
            return self._key_color if self._strobe_on else QColor("#1a1a1a")
        else:
            return self._key_color

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        # ── Chassis ──
        chassis_rect = QRectF(
            _CHASSIS_MARGIN, _CHASSIS_MARGIN,
            w - 2 * _CHASSIS_MARGIN, h - 2 * _CHASSIS_MARGIN,
        )
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor("#1a1a1a"))
        grad.setColorAt(1.0, QColor("#121212"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(chassis_rect, 8, 8)

        # Pre-compute row heights from the chassis interior
        n_rows = len(_KEYBOARD)
        chassis_w = w - 2 * _CHASSIS_MARGIN
        chassis_h = h - 2 * _CHASSIS_MARGIN
        inner_h = chassis_h - 2 * _CHASSIS_PAD
        row_h = (inner_h - (n_rows - 1) * _GAP_PX) / n_rows

        # Compute the unit width from a main row (15u), but the first row
        # (function keys) is narrower — we'll center it.
        main_inner_w = chassis_w - 2 * _CHASSIS_PAD
        unit = main_inner_w / _MAIN_ROW_WIDTH

        base_x = _CHASSIS_MARGIN + _CHASSIS_PAD
        base_y = _CHASSIS_MARGIN + _CHASSIS_PAD

        for row_idx, row in enumerate(_KEYBOARD):
            # Total flex width for this row
            total_flex = sum(kw for _, kw, _ in row) + sum(kg for _, _, kg in row)
            row_w = total_flex * unit
            if row_idx == 0:
                # Center the function row
                x = base_x + (main_inner_w - row_w) / 2
            else:
                x = base_x
            y = base_y + row_idx * (row_h + _GAP_PX)

            flex_x = 0.0  # running flex-space x for hue computation
            for label, kw, gap in row:
                x += gap * unit
                key_w = kw * unit - _GAP_PX
                key_rect = QRectF(x, y, key_w, row_h)

                # Compute per-key color
                color = self._key_color_at(flex_x + kw / 2, float(row_idx))

                # Glow under lit keys (skip dark/disabled)
                if self._enabled and color.value() > 30:
                    glow_rect = key_rect.adjusted(-1, -1, 2, 2)
                    glow = QColor(color)
                    glow.setAlpha(40)
                    painter.setBrush(glow)
                    painter.setPen(Qt.NoPen)
                    painter.drawRoundedRect(glow_rect, _KEY_RADIUS + 2, _KEY_RADIUS + 2)

                # Key body
                painter.setBrush(color)
                border = QColor(max(color.red() + 20, color.red()),
                                max(color.green() + 20, color.green()),
                                max(color.blue() + 20, color.blue())) if self._enabled \
                    else QColor("#3a3a3a")
                painter.setPen(border)
                painter.setBrush(color)
                painter.drawRoundedRect(key_rect, _KEY_RADIUS, _KEY_RADIUS)

                # Label
                if label:
                    is_big = len(str(label)) <= 1
                    font_size = int(row_h * 0.32) if is_big else int(row_h * 0.22)
                    font = QFont("DejaVu Sans", font_size)
                    font.setBold(not is_big)
                    painter.setFont(font)
                    # Text color: light on dark keys, dark on bright keys
                    brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
                    text_color = QColor("#1a1a1a") if brightness > 140 else QColor("#e0e0e0")
                    if not self._enabled:
                        text_color = QColor("#555555")
                    painter.setPen(text_color)
                    painter.drawText(key_rect, Qt.AlignCenter, label)

                x += kw * unit
                flex_x += kw + gap

        painter.end()


class KeyboardPage(QWidget):
    """Keyboard lighting tab with controls and visual preview."""

    enabled_changed = Signal(bool)
    effect_changed = Signal(str)
    color_changed = Signal(str)
    speed_changed = Signal(int)
    idle_timeout_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Load settings
        s = read_lighting_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Title
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

        # Idle timeout
        idle_label = QLabel("Idle timeout (s)")
        idle_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        ctrl_layout.addWidget(idle_label)

        self._idle_timeout_spin = QSpinBox(controls)
        self._idle_timeout_spin.setRange(0, 600)
        self._idle_timeout_spin.setValue(s.idle_timeout)
        self._idle_timeout_spin.setSuffix(" s")
        self._idle_timeout_spin.setSpecialValueText("Off")
        self._idle_timeout_spin.setFixedWidth(80)
        self._idle_timeout_spin.valueChanged.connect(self._on_idle_timeout_changed)
        ctrl_layout.addWidget(self._idle_timeout_spin)

        ctrl_layout.addStretch()
        layout.addWidget(controls)
        self._settings = s


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
        self.speed_changed.emit(value)

    def _on_idle_timeout_changed(self, value: int):
        self._settings.idle_timeout = max(0, value)
        write_lighting_settings(self._settings)
        self.idle_timeout_changed.emit(self._settings.idle_timeout)
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