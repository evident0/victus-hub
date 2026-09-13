"""Hue and shade colour strips, copied from Ohman's StripPicker."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from victus_hub.app.theme import hsl


class StripPicker(QWidget):
    """A strip of colour cells: the hue row, or the shades of one hue."""

    picked = Signal(str)  # #rrggbb

    def __init__(self, cells: int = 36, shade: bool = False, parent=None):
        super().__init__(parent)
        self._cells = cells
        self._shade = shade
        self._hue = 210.0
        self._current = QColor()
        self._has_current = False
        self.setFixedHeight(33)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

    def set_hue(self, hue: float) -> None:
        self._hue = hue
        self.update()

    def set_current(self, color: QColor | str | None) -> None:
        if color is None:
            self._has_current = False
            self.update()
            return
        self._current = QColor(color)
        self._has_current = self._current.isValid()
        self.update()

    def color_at(self, i: int) -> QColor:
        if not self._shade:
            return hsl(i * 360.0 / self._cells, 0.85, 0.55)
        t = 0.0 if self._cells <= 1 else i / (self._cells - 1)
        return hsl(self._hue, 0.28 + t * 0.6, 0.95 - t * 0.76)

    def _nearest(self) -> int:
        if not self._has_current:
            return -1
        best, bd = -1, 1e9
        cr, cg, cb = self._current.red(), self._current.green(), self._current.blue()
        for i in range(self._cells):
            c = self.color_at(i)
            d = (c.red() - cr) ** 2 + (c.green() - cg) ** 2 + (c.blue() - cb) ** 2
            if d < bd:
                bd, best = d, i
        return best if bd <= 1200 else -1

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width() / self._cells
        h = self.height()
        for i in range(self._cells):
            p.fillRect(int(i * w), 0, int(w + 1), int(h), self.color_at(i))
        sel = self._nearest()
        if sel >= 0:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#FCFAF9"), 2))
            p.drawRect(int(sel * w + 1), 1, max(1, int(w - 2)), int(h - 2))
        p.end()

    def _pick_at(self, x: float) -> None:
        i = max(0, min(self._cells - 1, int(x / (self.width() / self._cells))))
        color = self.color_at(i)
        self.set_current(color)
        self.picked.emit(color.name())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pick_at(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._pick_at(event.position().x())
