"""Live sensor line graph widget with fill area and hover tooltip."""

from collections import deque

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QPainterPath, QMouseEvent,
)

from hp_helper.app.theme import COLORS


class SensorLineGraph(QWidget):
    """Custom widget drawing a live line chart with fill, grid, and hover tooltip."""

    def __init__(self, sample_capacity: int = 500, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self._samples: deque[float | None] = deque([None] * sample_capacity, maxlen=sample_capacity)
        self._value_min = 0.0
        self._value_max = 100.0
        self._value_unit = "\u00B0C"
        self._accent = QColor("#f04b4b")
        self._compact = compact
        self._hovered = False
        self._hover_index = -1
        self._hover_value = 0.0

        self.setMouseTracking(True)
        self.setMinimumHeight(80 if compact else 160)

    # ── Properties ──

    def set_value_range(self, vmin: float, vmax: float):
        self._value_min = vmin
        self._value_max = max(vmin + 1, vmax)
        self.update()

    def set_unit(self, unit: str):
        self._value_unit = unit
        self.update()

    def set_accent(self, color: QColor):
        self._accent = color

    def append_sample(self, value: float | None):
        """Add a new sample to the ring buffer."""
        self._samples.append(value)
        self.update()

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        n = len(self._samples)
        if n < 2:
            painter.end()
            return

        vmin = self._value_min
        vmax = self._value_max
        vrange = vmax - vmin if vmax > vmin else 1.0

        # Coordinate transforms
        def x_for(i: int) -> float:
            return (i / (n - 1)) * w

        def y_for(v: float) -> float:
            return h - ((v - vmin) / vrange) * h

        # Background
        painter.setBrush(QColor("#202020"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(0, 0, w, h), 4, 4)

        # Grid lines — fewer divisions in compact mode
        fracs = (0.0, 0.5, 1.0) if self._compact else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
        for frac in fracs:
            y = frac * h
            is_edge = (frac == 0.0 or frac == 1.0)
            painter.setPen(QPen(QColor("#444444") if is_edge else QColor("#3a3a3a"), 1))
            painter.drawLine(QPointF(0, y), QPointF(w, y))

        # Left + right border
        painter.setPen(QPen(QColor("#444444"), 1))
        painter.drawLine(QPointF(0, 0), QPointF(0, h))
        painter.drawLine(QPointF(w, 0), QPointF(w, h))

        # Build step-chart segments between consecutive non-None samples
        self._segments: list[dict] = []
        valid_indices = [i for i, v in enumerate(self._samples) if v is not None]
        for j in range(1, len(valid_indices)):
            li = valid_indices[j - 1]
            ri = valid_indices[j]
            lv = self._samples[li]
            rv = self._samples[ri]
            if li + 1 <= ri:
                self._segments.append({
                    "leftIndex": li,
                    "leftValue": lv,
                    "rightIndex": ri,
                    "rightValue": rv,
                })

        if len(self._segments) >= 1:
            first = self._segments[0]
            last = self._segments[-1]

            # ── Fill area (step chart: horizontal at left val, vertical to right val) ──
            fill_path = QPainterPath()
            fill_path.moveTo(x_for(first["leftIndex"]), y_for(first["leftValue"]))
            for seg in self._segments:
                fill_path.lineTo(x_for(seg["rightIndex"]), y_for(seg["leftValue"]))
                fill_path.lineTo(x_for(seg["rightIndex"]), y_for(seg["rightValue"]))
            fill_path.lineTo(x_for(last["rightIndex"]), h)
            fill_path.lineTo(x_for(first["leftIndex"]), h)
            fill_path.closeSubpath()

            fill_color = QColor(self._accent)
            fill_color.setAlpha(96 if self._compact else 128)
            painter.setBrush(fill_color)
            painter.setPen(Qt.NoPen)
            painter.drawPath(fill_path)

            # ── Top polyline (step chart) ──
            line_path = QPainterPath()
            line_path.moveTo(x_for(first["leftIndex"]), y_for(first["leftValue"]))
            for seg in self._segments:
                line_path.lineTo(x_for(seg["rightIndex"]), y_for(seg["leftValue"]))
                line_path.lineTo(x_for(seg["rightIndex"]), y_for(seg["rightValue"]))

            painter.setPen(QPen(self._accent, 1.5 if self._compact else 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(line_path)


        # Hover tooltip — show plateau value at the hovered x position
        if self._hovered and self._hover_index >= 0:
            # Find which segment covers this index to get the plateau value
            seg = next((s for s in self._segments if s["leftIndex"] <= self._hover_index <= s["rightIndex"]), None)
            if seg is not None:
                v = seg["leftValue"]
                hx = x_for(self._hover_index)
                hy = y_for(v)
                label = f"{v:.1f} {self._value_unit}"
                tip_w = len(label) * 10 + 14
                tip_h = 22
                tip_x = hx + 12
                if tip_x + tip_w > w:
                    tip_x = hx - tip_w - 4
                tip_y = hy - 28
                if tip_y < 4:
                    tip_y = hy + 8

                painter.setBrush(QColor("#303030"))
                painter.setPen(QPen(self._accent, 1))
                painter.drawRoundedRect(QRectF(tip_x, tip_y, tip_w, tip_h), 4, 4)
                painter.setPen(QColor("#ffffff"))
                font = QFont()
                font.setPointSize(10)
                painter.setFont(font)
                painter.drawText(QRectF(tip_x + 6, tip_y, tip_w - 12, tip_h),
                                 Qt.AlignVCenter, label)

    def mouseMoveEvent(self, event: QMouseEvent):
        mx = event.position().x()
        n = len(self._samples)
        if n < 2:
            return
        idx = round((mx / self.width()) * (n - 1))
        idx = max(0, min(n - 1, idx))
        # Check if any step-chart segment covers this index
        covered = any(s["leftIndex"] <= idx <= s["rightIndex"] for s in getattr(self, "_segments", []))
        if covered:
            self._hovered = True
            self._hover_index = idx
            self.update()
        else:
            self._hovered = False
            self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
