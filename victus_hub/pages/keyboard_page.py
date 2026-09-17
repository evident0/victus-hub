"""Keyboard page with lighting controls and visual keyboard preview.

Single-zone hardware keeps one color picker. Multi-zone (4-zone) hardware
shows independent pickers for Right / Center / Left / WASD. Software
effects (breathing, wave, cycle, …) are computed in userspace.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QColorDialog, QSlider, QLineEdit,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont

from victus_hub import api
from victus_hub.app.theme import COLORS, UI_FONT, mono_font, ui_font
from victus_hub.pages.settings_page import make_spin
from victus_hub.widgets.chrome import PageHead, SettingsRow, hairline
from victus_hub.widgets.seg import LinkSeg, Seg
from victus_hub.widgets.strip_picker import StripPicker
from victus_hub.widgets.toggle_switch import ToggleSwitch
from victus_hub.backend.modules import keyboard_rgb_module
from victus_hub.features.keyboard.lighting import (
    EFFECTS_IGNORE_COLOR,
    EFFECTS_NEED_COLOR2,
    ZONE_NAMES,
    effects_for_zone_count,
    normalize_lighting_settings,
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
    """Visual keyboard preview showing per-zone color(s) (or off)."""

    def __init__(self, parent=None, zone_count: int = 1, compact: bool = False):
        super().__init__(parent)
        self._zone_count = max(1, zone_count)
        self._zone_colors = [QColor(COLORS["pill"])] * self._zone_count
        self._enabled = False
        self._compact = compact
        if compact:
            self.setMinimumHeight(56)
            self.setMaximumHeight(72)
        else:
            self.setMinimumHeight(200)
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
        compact = self._compact
        margin = 0 if compact else 2
        gap = 2.0 if compact else _GAP_PX
        radius = 3.0 if compact else 6.0

        n_rows = len(_KEYBOARD)
        inner_w = w - 2 * margin
        inner_h = h - 2 * margin
        unit_w = inner_w / _MAIN_ROW_WIDTH
        row_h_limit = (inner_h - (n_rows - 1) * gap) / n_rows
        unit_h = row_h_limit + gap
        unit = min(unit_w, unit_h)
        row_h = unit - gap

        block_w = _MAIN_ROW_WIDTH * unit
        block_h = n_rows * unit - gap
        base_x = margin + (inner_w - block_w) / 2
        base_y = margin + (inner_h - block_h) / 2

        for row_idx, row in enumerate(_KEYBOARD):
            total_flex = sum(kw for _, kw, _ in row) + sum(kg for _, _, kg in row)
            row_w = total_flex * unit
            if row_idx == 0:
                x = base_x + (block_w - row_w) / 2
            else:
                x = base_x
            y = base_y + row_idx * (row_h + gap)

            flex_x = 0.0
            for label, kw, gap_u in row:
                x += gap_u * unit
                key_w = kw * unit - gap
                key_rect = QRectF(x, y, key_w, row_h)

                key_center_x = flex_x + gap_u + kw / 2
                color = self._color_for_key(label, key_center_x)
                if not self._enabled:
                    color = QColor(COLORS["pill"])

                if self._enabled and color.value() > 30 and not compact:
                    glow = QColor(color)
                    glow.setAlpha(36)
                    painter.setBrush(glow)
                    painter.setPen(Qt.NoPen)
                    painter.drawRoundedRect(key_rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)

                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(key_rect, radius, radius)

                if label and not compact:
                    is_big = len(str(label)) <= 1
                    font_size = max(7, int(row_h * (0.34 if is_big else 0.22)))
                    font = QFont(UI_FONT)
                    font.setPixelSize(font_size)
                    painter.setFont(font)
                    brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
                    text_color = QColor(COLORS["bg"]) if brightness > 150 else QColor(COLORS["text"])
                    if not self._enabled:
                        text_color = QColor("#5C5C5C")
                    painter.setPen(text_color)
                    painter.drawText(key_rect, Qt.AlignCenter, label)

                x += kw * unit
                flex_x += kw + gap_u

        painter.end()


class KeyboardPage(QWidget):
    """Keyboard lighting tab with controls and visual preview."""

    enabled_changed = Signal(bool)
    effect_changed = Signal(str)
    speed_changed = Signal(int)
    color_changed = Signal(str)           # single-zone / primary color
    color2_changed = Signal(str)          # wave / gradient secondary
    zone_color_changed = Signal(int, str)  # multi-zone: (zone_index, hex)
    idle_timeout_changed = Signal(int)
    brightness_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zone_count = api.get_keyboard_zone_count()
        # Load settings
        s = normalize_lighting_settings(read_lighting_settings(), self._zone_count)
        self._zone_hexes = normalize_zone_colors(
            s.color, s.zone_colors, self._zone_count,
        )
        if self._zone_count > 1:
            s.zone_colors = list(self._zone_hexes)
            s.color = self._zone_hexes[0]
        self._settings = s

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        kbd_text, _kbd_color = keyboard_rgb_module()
        self._head = PageHead("Keyboard")
        self._head.set_status(kbd_text)
        layout.addWidget(self._head)

        self._effect_items = [("off", "Off")] + list(effects_for_zone_count(self._zone_count))
        names = [label for _value, label in self._effect_items]
        # Two rows if the list is long, so the compact panel still fits.
        chunk = 6 if len(names) > 7 else len(names)
        self._effect_links: list[LinkSeg] = []
        self._effect_offset: list[int] = []
        links_col = QVBoxLayout()
        links_col.setContentsMargins(0, 24, 0, 0)
        links_col.setSpacing(10)
        for start in range(0, len(names), chunk):
            seg = LinkSeg(names[start:start + chunk], gap=16, size=14)
            offset = start
            seg.picked.connect(lambda i, off=offset: self._on_effect_link(off + i))
            links_col.addWidget(seg)
            self._effect_links.append(seg)
            self._effect_offset.append(offset)
        layout.addLayout(links_col)
        if s.enabled:
            try:
                effect_idx = 1 + [v for v, _ in self._effect_items[1:]].index(s.effect)
            except ValueError:
                effect_idx = 1
        else:
            effect_idx = 0
        self._select_effect_link(effect_idx, False)

        self._zone_wrap = QWidget()
        zone_l = QHBoxLayout(self._zone_wrap)
        zone_l.setContentsMargins(0, 22, 0, 0)
        zone_l.setSpacing(10)
        sel_lbl = QLabel("Select")
        sel_lbl.setFont(ui_font(13))
        sel_lbl.setStyleSheet(f"color: {COLORS['seg_text']}; background: transparent;")
        zone_l.addWidget(sel_lbl)
        zone_names = list(ZONE_NAMES[: self._zone_count]) + ["All"]
        self._gran = Seg(zone_names, kind="compact") if self._zone_count > 1 else None
        self._zone_target = 0  # 0..n-1 zone, n = all
        if self._gran is not None:
            self._gran.picked.connect(self._on_gran)
            self._gran.select(self._zone_count, False)  # All
            self._zone_target = self._zone_count
            zone_l.addWidget(self._gran)
        zone_l.addStretch()
        self._zone_wrap.setVisible(self._zone_count > 1)
        layout.addWidget(self._zone_wrap)

        self._visual = KeyboardVisual(zone_count=self._zone_count)
        vis_wrap = QWidget()
        vis_l = QVBoxLayout(vis_wrap)
        vis_l.setContentsMargins(0, 22, 0, 0)
        vis_l.addWidget(self._visual)
        layout.addWidget(vis_wrap, 1)
        self._apply_visual_from_settings()

        editor = QWidget()
        ed = QVBoxLayout(editor)
        ed.setContentsMargins(0, 22, 0, 0)
        ed.setSpacing(12)
        ed.addWidget(hairline())

        self._color_editor = QWidget()
        ce = QHBoxLayout(self._color_editor)
        ce.setContentsMargins(0, 18, 0, 0)
        ce.setSpacing(12)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        hex_row = QHBoxLayout()
        hex_row.setContentsMargins(0, 0, 0, 0)
        hex_row.setSpacing(0)
        self._hex_chip = QLabel()
        self._hex_chip.setFixedSize(40, 34)
        self._hex_chip.setCursor(Qt.PointingHandCursor)
        self._hex_chip.mousePressEvent = lambda e: self._pick_color()  # type: ignore
        hex_field = QWidget()
        hex_field.setFixedHeight(34)
        hex_field.setStyleSheet(
            f"background-color: {COLORS['well']}; border: 1px solid {COLORS['edge']};"
        )
        hf = QHBoxLayout(hex_field)
        hf.setContentsMargins(0, 0, 10, 0)
        hf.setSpacing(0)
        hf.addWidget(self._hex_chip)
        hash_lbl = QLabel("  #")
        hash_lbl.setFont(mono_font(13))
        hash_lbl.setStyleSheet(f"color: {COLORS['foot']}; background: transparent;")
        hf.addWidget(hash_lbl)
        self._hex_edit = QLineEdit(s.color.lstrip("#"))
        self._hex_edit.setMaxLength(6)
        self._hex_edit.setFrame(False)
        self._hex_edit.setFont(mono_font(13))
        self._hex_edit.setStyleSheet(
            f"background: transparent; color: {COLORS['text']}; padding: 0;"
        )
        self._hex_edit.editingFinished.connect(self._on_hex_typed)
        hf.addWidget(self._hex_edit, 1)
        hex_row.addWidget(hex_field, 1)
        left.addLayout(hex_row)

        level_row = QHBoxLayout()
        level_row.setContentsMargins(0, 0, 0, 0)
        level_row.setSpacing(8)
        self._brightness_slider = QSlider(Qt.Horizontal)
        self._brightness_slider.setRange(0, 255)
        self._brightness_slider.setValue(s.brightness)
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)
        level_row.addWidget(self._brightness_slider, 1)
        self._txt_level = QLabel(f"{round(s.brightness * 100 / 255)}%")
        self._txt_level.setFont(mono_font(11))
        self._txt_level.setFixedWidth(36)
        self._txt_level.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._txt_level.setStyleSheet(f"color: {COLORS['hex']}; background: transparent;")
        level_row.addWidget(self._txt_level)
        left.addLayout(level_row)
        ce.addLayout(left, 0)

        strips = QVBoxLayout()
        strips.setContentsMargins(0, 0, 0, 0)
        strips.setSpacing(0)
        self._hue = StripPicker(cells=36, shade=False)
        self._shade = StripPicker(cells=36, shade=True)
        self._hue.picked.connect(self._on_strip_color)
        self._shade.picked.connect(self._on_strip_color)
        strips.addWidget(self._hue)
        strips.addWidget(self._shade)
        ce.addLayout(strips, 1)
        ed.addWidget(self._color_editor)

        self._color2_wrap = QWidget()
        c2 = QHBoxLayout(self._color2_wrap)
        c2.setContentsMargins(0, 0, 0, 0)
        c2.setSpacing(8)
        self._primary_label = QLabel("Color 2")
        self._primary_label.setFont(ui_font(13))
        self._primary_label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        c2.addWidget(self._primary_label)
        self._color2_btn = QPushButton()
        self._color2_btn.setFixedSize(34, 28)
        self._color2_btn.setStyleSheet(_style_color_btn(s.color2))
        self._color2_btn.clicked.connect(self._pick_color2)
        c2.addWidget(self._color2_btn)
        c2.addStretch()
        ed.addWidget(self._color2_wrap)

        self._effect_editor = QWidget()
        ee = QHBoxLayout(self._effect_editor)
        ee.setContentsMargins(0, 0, 0, 0)
        ee.setSpacing(16)
        spd_lbl = QLabel("Speed")
        spd_lbl.setFont(ui_font(13))
        spd_lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        ee.addWidget(spd_lbl)
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(1, 100)
        self._speed_slider.setValue(s.speed)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        ee.addWidget(self._speed_slider, 1)
        self._txt_speed = QLabel(str(s.speed))
        self._txt_speed.setFont(mono_font(12))
        self._txt_speed.setFixedWidth(32)
        self._txt_speed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._txt_speed.setStyleSheet(f"color: {COLORS['hex']}; background: transparent;")
        ee.addWidget(self._txt_speed)
        ed.addWidget(self._effect_editor)

        # Hidden aliases so older handler fields still exist.
        self._enable_check = ToggleSwitch("")
        self._enable_check.hide()
        self._enable_check.setChecked(s.enabled)
        self._color_btn = QPushButton()
        self._color_btn.hide()
        self._zone_btns = []
        self._primary_wrap = self._color_editor
        self._colors_row = self._color_editor

        layout.addWidget(editor)

        idle_on = s.idle_timeout > 0
        idle_value = s.idle_timeout if idle_on else 30
        idle_ctrl = QWidget()
        idle_l = QHBoxLayout(idle_ctrl)
        idle_l.setContentsMargins(0, 0, 0, 0)
        idle_l.setSpacing(10)
        self._idle_timeout = make_spin("Idle timeout", "s", idle_value, 1, 600, compact=True)
        self._idle_timeout._spin.setEnabled(idle_on)
        self._idle_timeout._spin.valueChanged.connect(self._on_idle_timeout_changed)
        idle_l.addLayout(self._idle_timeout)
        self._idle_check = ToggleSwitch("", big=False)
        self._idle_check.setChecked(idle_on)
        self._idle_check.toggled.connect(self._on_idle_enabled_changed)
        idle_l.addWidget(self._idle_check)
        layout.addWidget(SettingsRow(
            "Idle timeout",
            "Dim the backlight when you stop typing",
            idle_ctrl,
        ))

        self._set_hex_chip(s.color)
        self._hue.set_current(s.color)
        self._shade.set_current(s.color)
        self._sync_effect_controls()

    def step_brightness(self, direction: int) -> None:
        """Move to the next 0/25/50/75/100% level, including from slider values."""
        levels = (0, 64, 128, 191, 255)
        current = self._settings.brightness if self._settings.enabled else 0
        if direction > 0:
            level = next((v for v in levels if v > current), 255)
        else:
            level = next((v for v in reversed(levels) if v < current), 0)
        self._brightness_slider.setValue(level)
        if level > 0 and not self._settings.enabled:
            index = next((i for i, (value, _) in enumerate(self._effect_items)
                          if value == self._settings.effect), 1)
            self._on_effect_link(index)

    def step_animation(self, direction: int) -> None:
        """Cycle supported lighting effects, including Off, in UI order."""
        effects = [value for value, _ in self._effect_items]
        if not effects:
            return
        current = self._settings.effect if self._settings.enabled else "off"
        if current not in effects:
            index = 0 if direction > 0 else len(effects) - 1
        else:
            index = (effects.index(current) + direction) % len(effects)
        self._on_effect_link(index)

    def _select_effect_link(self, index: int, animate: bool) -> None:
        for seg, offset in zip(self._effect_links, self._effect_offset):
            local = index - offset
            n = len(seg._labels)
            if 0 <= local < n:
                seg.select(local, animate)
            else:
                seg.select(-1, False) if False else None
                for i, lab in enumerate(seg._labels):
                    lab.setStyleSheet(
                        f"color: {COLORS['desc']}; background: transparent;"
                    )
                seg._sel = -1
                seg._place(False)

    def _on_effect_link(self, index: int) -> None:
        self._select_effect_link(index, True)
        value, _label = self._effect_items[index]
        if value == "off":
            if self._settings.enabled:
                self._enable_check.blockSignals(True)
                self._enable_check.setChecked(False)
                self._enable_check.blockSignals(False)
                self._on_enabled_changed(False)
            return
        if not self._settings.enabled:
            self._enable_check.blockSignals(True)
            self._enable_check.setChecked(True)
            self._enable_check.blockSignals(False)
            self._settings.enabled = True
            self._persist()
            self.enabled_changed.emit(True)
        self._settings.effect = str(value)
        self._persist()
        self._sync_effect_controls()
        self._apply_visual_from_settings()
        self.effect_changed.emit(self._settings.effect)

    def _on_gran(self, index: int) -> None:
        self._zone_target = index

    def _set_hex_chip(self, hex_str: str) -> None:
        self._hex_chip.setStyleSheet(f"background-color: {hex_str};")
        self._hex_edit.blockSignals(True)
        self._hex_edit.setText(hex_str.lstrip("#").upper())
        self._hex_edit.blockSignals(False)
        hue = QColor(hex_str).hue()
        if hue >= 0:
            self._shade.set_hue(hue)
        self._hue.set_current(hex_str)
        self._shade.set_current(hex_str)

    def _apply_picked_hex(self, hex_str: str) -> None:
        if self._zone_count > 1 and self._zone_target < self._zone_count:
            self._zone_hexes[self._zone_target] = hex_str
            if self._zone_target == 0:
                self._settings.color = hex_str
            self._persist()
            self._apply_visual_from_settings()
            self.zone_color_changed.emit(self._zone_target, hex_str)
        else:
            self._settings.color = hex_str
            if self._zone_count <= 1:
                self._zone_hexes = [hex_str]
            else:
                self._zone_hexes = [hex_str] * self._zone_count
            self._persist()
            self._apply_visual_from_settings()
            self.color_changed.emit(hex_str)
            if self._zone_count > 1:
                for i, h in enumerate(self._zone_hexes):
                    self.zone_color_changed.emit(i, h)
        self._set_hex_chip(hex_str)

    def _on_strip_color(self, hex_str: str) -> None:
        self._apply_picked_hex(hex_str)

    def _on_hex_typed(self) -> None:
        raw = self._hex_edit.text().strip().lstrip("#")
        if len(raw) != 6:
            self._hex_edit.setText(self._settings.color.lstrip("#").upper())
            return
        try:
            int(raw, 16)
        except ValueError:
            self._hex_edit.setText(self._settings.color.lstrip("#").upper())
            return
        self._apply_picked_hex("#" + raw.upper())

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

    def _sync_effect_controls(self) -> None:
        effect = self._settings.effect
        enabled = self._settings.enabled
        animated = enabled and effect != "static"
        ignore_color = (not enabled) or effect in EFFECTS_IGNORE_COLOR
        need_color2 = enabled and effect in EFFECTS_NEED_COLOR2
        show_color = enabled and not ignore_color
        self._effect_editor.setVisible(animated)
        self._speed_slider.setEnabled(animated)
        self._color_editor.setVisible(show_color)
        self._color2_wrap.setVisible(need_color2)
        self._zone_wrap.setVisible(enabled and self._zone_count > 1 and show_color)
        zones = self._zone_count if self._zone_count > 1 else 1
        status = "off" if not enabled else f"{zones} zone{'s' if zones != 1 else ''} · {effect.replace('_', ' ')}"
        self._head.set_status(status)

    def _on_enabled_changed(self, checked: bool):
        self._settings.enabled = checked
        self._persist()
        self._apply_visual_from_settings()
        self._sync_effect_controls()
        self.enabled_changed.emit(checked)

    def _on_effect_changed(self, index: int):
        self._on_effect_link(index)

    def _on_speed_changed(self, value: int):
        self._settings.speed = max(1, min(100, value))
        self._speed_slider.setToolTip(f"Effect speed: {value}/100")
        self._txt_speed.setText(str(value))
        self._persist()
        self.speed_changed.emit(self._settings.speed)

    def _pick_color(self):
        """Primary / single-zone color picker."""
        current = QColor(self._settings.color)
        color = QColorDialog.getColor(current, self, "Keyboard Color")
        if color.isValid():
            hex_str = color.name()
            self._settings.color = hex_str
            if self._zone_count <= 1:
                self._zone_hexes = [hex_str]
            elif self._zone_hexes:
                self._zone_hexes[0] = hex_str
            self._set_hex_chip(hex_str)
            self._persist()
            self._apply_visual_from_settings()
            self.color_changed.emit(hex_str)

    def _pick_color2(self):
        current = QColor(self._settings.color2)
        color = QColorDialog.getColor(current, self, "Secondary Color")
        if not color.isValid():
            return
        hex_str = color.name()
        self._settings.color2 = hex_str
        if self._color2_btn is not None:
            self._color2_btn.setStyleSheet(_style_color_btn(hex_str))
        self._persist()
        self._apply_visual_from_settings()
        self.color2_changed.emit(hex_str)

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

    def _effective_idle_timeout(self) -> int:
        if not self._idle_check.isChecked():
            return 0
        return max(1, self._idle_timeout._spin.value())

    def _commit_idle_timeout(self) -> None:
        value = self._effective_idle_timeout()
        self._settings.idle_timeout = value
        self._persist()
        self.idle_timeout_changed.emit(value)

    def _on_idle_enabled_changed(self, checked: bool):
        spin = self._idle_timeout._spin
        if checked and spin.value() < 1:
            spin.blockSignals(True)
            spin.setValue(30)
            spin.blockSignals(False)
        spin.setEnabled(checked)
        self._commit_idle_timeout()

    def _on_idle_timeout_changed(self, value: int):
        if not self._idle_check.isChecked():
            return
        self._settings.idle_timeout = max(1, value)
        self._persist()
        self.idle_timeout_changed.emit(self._settings.idle_timeout)

    def _on_brightness_changed(self, value: int):
        self._settings.brightness = max(0, min(255, value))
        self._brightness_slider.setToolTip(f"Backlight brightness: {value}/255")
        self._txt_level.setText(f"{round(value * 100 / 255)}%")
        self._persist()
        self.brightness_changed.emit(value)

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
