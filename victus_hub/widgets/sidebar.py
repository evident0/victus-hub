"""62 px rail: sliding pill, stroke icons, settings at the bottom."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QByteArray, QEasingCurve, QPoint, QPropertyAnimation, QRect, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from victus_hub.app.theme import COLORS


def _gear_path(cx: float = 12, cy: float = 12, r_out: float = 10,
               r_in: float = 7.6, teeth: int = 8, hole: float = 3.4) -> str:
    parts: list[str] = []
    step = 2 * math.pi / teeth
    for i in range(teeth):
        a0 = i * step
        angs = (a0 + 0.06 * step, a0 + 0.44 * step, a0 + 0.56 * step, a0 + 0.94 * step)
        rads = (r_out, r_out, r_in, r_in)
        for k in range(4):
            x = cx + rads[k] * math.cos(angs[k])
            y = cy + rads[k] * math.sin(angs[k])
            cmd = "M" if i == 0 and k == 0 else "L"
            parts.append(f"{cmd}{x:.2f} {y:.2f}")
    parts.append("Z")
    parts.append(
        f"M{cx + hole:.2f} {cy:.2f} A{hole:.2f} {hole:.2f} 0 1 0 {cx - hole:.2f} {cy:.2f} "
        f"A{hole:.2f} {hole:.2f} 0 1 0 {cx + hole:.2f} {cy:.2f} Z"
    )
    return " ".join(parts)

RAIL_W = 62
BTN = 42
ICON = 18

# Ohman 24-unit icon geometry (scaled to 18 px). Extra pages keep the same stroke.
_ICONS: list[tuple[str, str, list[str], list[str]]] = [
    ("Home", "home",
     ["M12 3.5 A8.5 8.5 0 1 0 12 20.5 A8.5 8.5 0 1 0 12 3.5 Z"],
     ["M12 8.6 A3.4 3.4 0 1 0 12 15.4 A3.4 3.4 0 1 0 12 8.6 Z"]),
    ("Power", "power",
     [],
     ["M13 2 L4 14 L11 14 L10 22 L20 9 L13 9 Z"]),
    ("Fans", "fans",
     ["M3 8.5 C5.5 5.5 8.5 11.5 12 8.5 C15.5 5.5 18.5 11.5 21 8.5 "
      "M3 15.5 C5.5 12.5 8.5 18.5 12 15.5 C15.5 12.5 18.5 18.5 21 15.5"],
     []),
    ("Keyboard", "keyboard",
     ["M2.5 7.5 A2 2 0 0 1 4.5 5.5 H19.5 A2 2 0 0 1 21.5 7.5 V16.5 A2 2 0 0 1 19.5 18.5 H4.5 A2 2 0 0 1 2.5 16.5 Z M6 9.5 H6.4 M9.8 9.5 H10.2 M13.6 9.5 H14 M17.4 9.5 H17.8 M6 12.5 H6.4 M9.8 12.5 H10.2 M13.6 12.5 H14 M17.4 12.5 H17.8 M7.5 15.5 H16.5"],
     []),
    ("Sensors", "sensors",
     ["M10 14.2 V5 A2 2 0 0 1 14 5 V14.2 A4 4 0 1 1 10 14.2 Z M12 8 V17"],
     ["M12 15.5 A2.2 2.2 0 1 0 12 19.9 A2.2 2.2 0 1 0 12 15.5 Z"]),
    ("Processes", "processes",
     ["M5 8 H19 M5 12 H19 M5 16 H19"],
     []),
]

_SETTINGS = ("Settings", "settings", [], [_gear_path()])


def _svg(strokes: list[str], fills: list[str], color: str) -> QSvgRenderer:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    ]
    for d in strokes:
        parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    for d in fills:
        parts.append(f'<path d="{d}" fill="{color}" fill-rule="evenodd"/>')
    parts.append("</svg>")
    return QSvgRenderer(QByteArray("".join(parts).encode("utf-8")))


class _NavBtn(QWidget):
    clicked = Signal(int)

    def __init__(self, index: int, tip: str, strokes: list[str], fills: list[str],
                 parent=None):
        super().__init__(parent)
        self._index = index
        self._strokes = strokes
        self._fills = fills
        self._selected = False
        self.setFixedSize(BTN, BTN)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_selected(self, on: bool) -> None:
        if self._selected == on:
            return
        self._selected = on
        self.update()

    def paintEvent(self, _event):
        if self._selected:
            color = COLORS["text"]
        elif self.underMouse():
            color = COLORS["sub"]
        else:
            color = COLORS["axis"]
        renderer = _svg(self._strokes, self._fills, color)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRect((BTN - ICON) // 2, (BTN - ICON) // 2, ICON, ICON)
        renderer.render(p, box)
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index)


class _Pill(QWidget):
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["pill"]))
        p.drawRoundedRect(self.rect(), 12, 12)
        p.end()


class Sidebar(QWidget):
    """Vertical icon rail. Settings sits at the bottom; the rest stack from the top."""

    tab_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(RAIL_W)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(COLORS["sunken"]))
        self.setPalette(pal)

        self._buttons: list[_NavBtn] = []
        self._active_index = 0

        self._pill = _Pill(self)
        self._pill.setFixedSize(BTN, BTN)
        self._pill_anim = QPropertyAnimation(self._pill, b"pos", self)
        self._pill_anim.setDuration(280)
        self._pill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        layout = QVBoxLayout(self)
        layout.setContentsMargins((RAIL_W - BTN) // 2, 20, (RAIL_W - BTN) // 2, 20)
        layout.setSpacing(4)

        for i, (tip, _key, strokes, fills) in enumerate(_ICONS):
            btn = _NavBtn(i, tip, strokes, fills)
            btn.clicked.connect(self.set_active)
            layout.addWidget(btn, 0, Qt.AlignHCenter)
            self._buttons.append(btn)

        layout.addStretch(1)

        settings_index = len(_ICONS)
        tip, _key, strokes, fills = _SETTINGS
        settings = _NavBtn(settings_index, tip, strokes, fills)
        settings.clicked.connect(self.set_active)
        layout.addWidget(settings, 0, Qt.AlignHCenter)
        self._buttons.append(settings)

        self._buttons[0].set_selected(True)

    def set_active(self, index: int):
        if index < 0 or index >= len(self._buttons):
            return
        if index == self._active_index and self._pill.isVisible():
            return
        self._active_index = index
        for i, btn in enumerate(self._buttons):
            btn.set_selected(i == index)
        self._place_pill(True)
        self.tab_changed.emit(index)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["sunken"]))
        p.setPen(QColor(COLORS["line2"]))
        x = self.width() - 1
        p.drawLine(x, 0, x, self.height())
        p.end()

    def refresh_accent(self) -> None:
        self._pill.update()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._place_pill(False))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_pill(False)

    def _place_pill(self, animate: bool) -> None:
        btn = self._buttons[self._active_index]
        top_left = btn.mapTo(self, QPoint(0, 0))
        target = QPoint(top_left.x(), top_left.y())
        self._pill.resize(BTN, BTN)
        self._pill.lower()
        for b in self._buttons:
            b.raise_()
        if not animate or not self._pill.isVisible():
            self._pill_anim.stop()
            self._pill.move(target)
            self._pill.show()
            return
        self._pill.show()
        self._pill_anim.stop()
        self._pill_anim.setStartValue(self._pill.pos())
        self._pill_anim.setEndValue(target)
        self._pill_anim.start()
