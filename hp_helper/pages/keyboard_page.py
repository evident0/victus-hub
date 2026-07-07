"""Keyboard page with lighting controls and visual keyboard preview."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QPushButton, QColorDialog,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont

from hp_helper.widgets.section_title import SectionTitle
from hp_helper.theme import COLORS
from hp_helper.pages.settings_page import make_spin
from hp_helper.keyboard_lighting import (
    read_lighting_settings, write_lighting_settings,
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
    """Visual keyboard preview showing the current static color (or off)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key_color = QColor("#2a2a2a")
        self._enabled = False
        self.setMinimumHeight(240)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           self.sizePolicy().verticalPolicy())
    def set_key_state(self, enabled: bool, color: QColor):
        self._enabled = enabled
        self._key_color = color if enabled else QColor(0, 0, 0)
        self.update()

    # ── Key color computation (static only now) ──
    def _key_color_at(self, key_x: float, key_y: float) -> QColor:
        """Color for a single key (ignores position; static color)."""
        if not self._enabled:
            return QColor(0, 0, 0)
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
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["surface"]))
        painter.drawRoundedRect(chassis_rect, 8, 8)

        # Pre-compute row heights from the chassis interior
        n_rows = len(_KEYBOARD)
        chassis_w = w - 2 * _CHASSIS_MARGIN
        chassis_h = h - 2 * _CHASSIS_MARGIN
        inner_w = chassis_w - 2 * _CHASSIS_PAD
        inner_h = chassis_h - 2 * _CHASSIS_PAD

        # Compute unit from width
        unit_w = inner_w / _MAIN_ROW_WIDTH

        # Compute unit from height — enforce square 1u keys
        # row_h = (inner_h - (n_rows - 1) * _GAP_PX) / n_rows
        # square → unit - _GAP_PX == row_h  →  unit_h = row_h + _GAP_PX
        row_h_limit = (inner_h - (n_rows - 1) * _GAP_PX) / n_rows
        unit_h = row_h_limit + _GAP_PX

        unit = min(unit_w, unit_h)
        row_h = unit - _GAP_PX

        # Keyboard block dimensions for centering
        block_w = _MAIN_ROW_WIDTH * unit
        block_h = n_rows * unit - _GAP_PX

        base_x = _CHASSIS_MARGIN + _CHASSIS_PAD + (inner_w - block_w) / 2
        base_y = _CHASSIS_MARGIN + _CHASSIS_PAD + (inner_h - block_h) / 2

        for row_idx, row in enumerate(_KEYBOARD):
            # Total flex width for this row
            total_flex = sum(kw for _, kw, _ in row) + sum(kg for _, _, kg in row)
            row_w = total_flex * unit
            if row_idx == 0:
                # Center the function row within the block
                x = base_x + (block_w - row_w) / 2
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
                    else QColor("#1a1a1a")
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
    color_changed = Signal(str)
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
        self._visual.set_key_state(s.enabled, QColor(s.color))
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

        # Idle timeout
        self._idle_timeout = make_spin(
            "Idle timeout", "s",
            s.idle_timeout, 0, 600,
            compact=True,
        )
        self._idle_timeout._spin.setSpecialValueText("Off")
        self._idle_timeout._spin.valueChanged.connect(self._on_idle_timeout_changed)
        ctrl_layout.addLayout(self._idle_timeout)

        ctrl_layout.addStretch()
        layout.addWidget(controls)
        self._settings = s


    def _on_enabled_changed(self, checked: bool):
        self._settings.enabled = checked
        write_lighting_settings(self._settings)
        self._visual.set_key_state(checked, QColor(self._settings.color))
        self.enabled_changed.emit(checked)

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
            self._visual.set_key_state(self._settings.enabled, color)
            self.color_changed.emit(hex_str)

    def _on_idle_timeout_changed(self, value: int):
        self._settings.idle_timeout = max(0, value)
        write_lighting_settings(self._settings)
        self.idle_timeout_changed.emit(self._settings.idle_timeout)

    # ── Animation frame update (static only) ──
    def apply_frame(self, color_rgb):
        """Update the visual keyboard from a (static) frame color."""
        r, g, b = color_rgb.red, color_rgb.green, color_rgb.blue
        qc = QColor(r, g, b)
        is_off = max(r, g, b) == 0
        self._visual.set_key_state(not is_off, qc)