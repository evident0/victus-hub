"""Keyboard page with lighting controls and visual keyboard preview.

Single-zone hardware keeps one color picker. Multi-zone (4-zone) hardware
shows independent pickers for Right / Center / Left / WASD.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QPushButton, QColorDialog,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont

from hp_helper import api
from hp_helper.app.theme import COLORS
from hp_helper.pages.settings_page import make_spin
from hp_helper.features.keyboard.lighting import (
    ZONE_NAMES,
    normalize_zone_colors,
    read_lighting_settings,
    write_lighting_settings,
    zone_for_key,
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


def _style_color_btn(hex_str: str) -> str:
    return (
        f"background-color: {hex_str}; "
        f"border: 1px solid {COLORS['border']}; border-radius: 4px;"
    )


class KeyboardVisual(QWidget):
    """Visual keyboard preview showing static color(s) (or off)."""

    def __init__(self, parent=None, zone_count: int = 1):
        super().__init__(parent)
        self._zone_count = max(1, zone_count)
        self._zone_colors = [QColor("#2a2a2a")] * self._zone_count
        self._enabled = False
        self.setMinimumHeight(240)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           self.sizePolicy().verticalPolicy())

    def set_key_state(self, enabled: bool, color: QColor):
        """Single-zone helper: paint every key with one color."""
        self._enabled = enabled
        fill = color if enabled else QColor(0, 0, 0)
        self._zone_colors = [fill] * self._zone_count
        self.update()

    def set_zone_state(self, enabled: bool, colors: list[QColor]):
        """Multi-zone helper: one QColor per hardware zone."""
        self._enabled = enabled
        if not enabled:
            self._zone_colors = [QColor(0, 0, 0)] * self._zone_count
        else:
            filled: list[QColor] = []
            for i in range(self._zone_count):
                if i < len(colors):
                    filled.append(colors[i])
                elif colors:
                    filled.append(colors[-1])
                else:
                    filled.append(QColor("#2a2a2a"))
            self._zone_colors = filled
        self.update()

    def _color_for_key(self, label: str, key_center_x: float) -> QColor:
        if not self._enabled:
            return QColor(0, 0, 0)
        if self._zone_count <= 1:
            return self._zone_colors[0]
        zone = zone_for_key(label, key_center_x, _MAIN_ROW_WIDTH)
        if zone < 0 or zone >= len(self._zone_colors):
            return self._zone_colors[0]
        return self._zone_colors[zone]

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

            flex_x = 0.0  # running flex-space x for zone computation
            for label, kw, gap in row:
                x += gap * unit
                key_w = kw * unit - _GAP_PX
                key_rect = QRectF(x, y, key_w, row_h)

                key_center_x = flex_x + gap + kw / 2
                color = self._color_for_key(label, key_center_x)

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
    color_changed = Signal(str)           # single-zone color
    zone_color_changed = Signal(int, str)  # multi-zone: (zone_index, hex)
    idle_timeout_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zone_count = api.get_keyboard_zone_count()
        # Load settings
        s = read_lighting_settings()
        self._zone_hexes = normalize_zone_colors(
            s.color, s.zone_colors, self._zone_count,
        )
        if self._zone_count > 1:
            s.zone_colors = list(self._zone_hexes)
            s.color = self._zone_hexes[0]
        self._settings = s

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Title (same style as Power on Fans & Power page)
        title = QLabel("Keyboard Lighting")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        layout.addWidget(title)

        # Visual keyboard
        self._visual = KeyboardVisual(zone_count=self._zone_count)
        self._apply_visual_from_settings()
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

        self._color_btn: QPushButton | None = None
        self._zone_btns: list[QPushButton] = []

        if self._zone_count <= 1:
            # Single color (original UI)
            color_label = QLabel("Color")
            color_label.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 11px;"
            )
            ctrl_layout.addWidget(color_label)

            self._color_btn = QPushButton()
            self._color_btn.setFixedSize(34, 28)
            self._color_btn.setStyleSheet(_style_color_btn(s.color))
            self._color_btn.clicked.connect(self._pick_color)
            ctrl_layout.addWidget(self._color_btn)
        else:
            # One picker per hardware zone
            for zone_idx, name in enumerate(ZONE_NAMES[: self._zone_count]):
                zone_box = QVBoxLayout()
                zone_box.setContentsMargins(0, 0, 0, 0)
                zone_box.setSpacing(2)
                zone_label = QLabel(name)
                zone_label.setStyleSheet(
                    f"color: {COLORS['text_secondary']}; font-size: 11px;"
                )
                zone_label.setAlignment(Qt.AlignCenter)
                zone_box.addWidget(zone_label)

                btn = QPushButton()
                btn.setFixedSize(34, 28)
                btn.setToolTip(f"{name} zone color")
                hex_str = self._zone_hexes[zone_idx]
                btn.setStyleSheet(_style_color_btn(hex_str))
                btn.clicked.connect(
                    lambda _checked=False, z=zone_idx: self._pick_zone_color(z)
                )
                zone_box.addWidget(btn)
                self._zone_btns.append(btn)
                ctrl_layout.addLayout(zone_box)

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

    def _apply_visual_from_settings(self) -> None:
        if self._zone_count <= 1:
            self._visual.set_key_state(
                self._settings.enabled, QColor(self._settings.color),
            )
        else:
            colors = [QColor(h) for h in self._zone_hexes]
            self._visual.set_zone_state(self._settings.enabled, colors)

    def _persist(self) -> None:
        if self._zone_count > 1:
            self._settings.zone_colors = list(self._zone_hexes)
            self._settings.color = self._zone_hexes[0]
        write_lighting_settings(self._settings)

    def _on_enabled_changed(self, checked: bool):
        self._settings.enabled = checked
        self._persist()
        self._apply_visual_from_settings()
        self.enabled_changed.emit(checked)

    def _pick_color(self):
        """Single-zone color picker (original path)."""
        current = QColor(self._settings.color)
        color = QColorDialog.getColor(current, self, "Keyboard Color")
        if color.isValid():
            hex_str = color.name()
            self._settings.color = hex_str
            self._zone_hexes = [hex_str]
            if self._color_btn is not None:
                self._color_btn.setStyleSheet(_style_color_btn(hex_str))
            self._persist()
            self._visual.set_key_state(self._settings.enabled, color)
            self.color_changed.emit(hex_str)

    def _pick_zone_color(self, zone: int):
        """Multi-zone color picker for one zone."""
        if zone < 0 or zone >= len(self._zone_hexes):
            return
        current = QColor(self._zone_hexes[zone])
        name = ZONE_NAMES[zone] if zone < len(ZONE_NAMES) else f"Zone {zone}"
        color = QColorDialog.getColor(current, self, f"{name} Zone Color")
        if not color.isValid():
            return
        hex_str = color.name()
        self._zone_hexes[zone] = hex_str
        if zone < len(self._zone_btns):
            self._zone_btns[zone].setStyleSheet(_style_color_btn(hex_str))
        self._persist()
        self._apply_visual_from_settings()
        self.zone_color_changed.emit(zone, hex_str)

    def _on_idle_timeout_changed(self, value: int):
        self._settings.idle_timeout = max(0, value)
        self._persist()
        self.idle_timeout_changed.emit(self._settings.idle_timeout)

    # ── Animation frame update ──
    def apply_frame(self, frame):
        """Update the visual keyboard from controller frames.

        *frame* is a list of RgbColor (one per zone). A single RgbColor is
        still accepted for robustness.
        """
        if isinstance(frame, (list, tuple)):
            if not frame:
                self._visual.set_zone_state(False, [])
                return
            colors = [
                QColor(c.red, c.green, c.blue) for c in frame
            ]
            is_off = all(max(c.red(), c.green(), c.blue()) == 0 for c in colors)
            self._visual.set_zone_state(not is_off, colors)
            return

        # Legacy single RgbColor
        r, g, b = frame.red, frame.green, frame.blue
        qc = QColor(r, g, b)
        is_off = max(r, g, b) == 0
        self._visual.set_key_state(not is_off, qc)
