"""Interactive fan curve editor widget."""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics,
    QMouseEvent, QPainterPath,
)

from victus_hub.backend.types import FanPoint
from victus_hub.app.theme import COLORS

CHART_PADDING = 52
POINT_R = 7
HIT_R = 14
TEMP_MIN = 30



class FanChart(QWidget):
    """Interactive fan curve chart with draggable control points."""

    point_added = Signal(int, int)   # temp, speed
    point_moved = Signal(int, int, int)  # index, temp, speed
    point_deleted = Signal(int)      # index
    point_selected = Signal(int)     # index

    def __init__(self, title: str, accent: QColor, temp_max: int = 100,
                 points: list[FanPoint] | None = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._accent = accent
        self._temp_max = temp_max
        self._points: list[FanPoint] = points or [FanPoint(TEMP_MIN, 0), FanPoint(temp_max, 100)]
        self._selected_point = -1
        self._hovered_index = -1
        self._dragging = False

        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setStyleSheet(f"background-color: {COLORS['surface']}; border-radius: 4px;")

    # ── Properties ──

    @property
    def points(self) -> list[FanPoint]:
        return list(self._points)

    @points.setter
    def points(self, value: list[FanPoint]):
        self._points = list(value)
        self.update()

    @property
    def selected_point(self) -> int:
        return self._selected_point

    @selected_point.setter
    def selected_point(self, value: int):
        self._selected_point = value
        self.update()

    @property
    def segments(self) -> list[dict]:
        """Computed line segments between consecutive points."""
        segs = []
        for i in range(1, len(self._points)):
            left, right = self._points[i - 1], self._points[i]
            segs.append({
                "leftTemp": left.temp,
                "leftSpeed": left.speed,
                "rightTemp": right.temp,
                "rightSpeed": right.speed,
            })
        return segs

    # ── Coordinate transforms ──

    def _plot_bounds(self):
        w = self.width()
        h = self.height()
        return CHART_PADDING, CHART_PADDING, w - CHART_PADDING, h - CHART_PADDING

    def _temp_to_x(self, temp: float, bounds=None) -> float:
        pl, _, pr, _ = bounds or self._plot_bounds()
        pw = pr - pl
        return pl + ((temp - TEMP_MIN) / (self._temp_max - TEMP_MIN)) * pw

    def _speed_to_y(self, speed: float, bounds=None) -> float:
        _, pt, _, pb = bounds or self._plot_bounds()
        ph = pb - pt
        return pt + ((100 - speed) / 100.0) * ph

    def _x_to_temp(self, x: float, bounds=None) -> int:
        pl, _, pr, _ = bounds or self._plot_bounds()
        pw = pr - pl
        t = TEMP_MIN + ((x - pl) / pw) * (self._temp_max - TEMP_MIN)
        return round(max(TEMP_MIN, min(self._temp_max, t)))

    def _y_to_speed(self, y: float, bounds=None) -> int:
        _, pt, _, pb = bounds or self._plot_bounds()
        ph = pb - pt
        s = 100 - ((y - pt) / ph) * 100.0
        return round(max(0, min(100, s)))

    def _find_nearby_point(self, mx: float, my: float) -> int:
        for i, point in enumerate(self._points):
            px = self._temp_to_x(point.temp)
            py = self._speed_to_y(point.speed)
            if ((mx - px) ** 2 + (my - py) ** 2) ** 0.5 < HIT_R:
                return i
        return -1

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        pl, pt, pr, pb = self._plot_bounds()
        pw = pr - pl
        ph = pb - pt

        # Title
        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(QRectF(0, 4, w, 18), Qt.AlignCenter, self._title)

        # Y-axis labels (speed %)
        label_font = QFont()
        label_font.setPointSize(9)
        painter.setFont(label_font)
        painter.setPen(QColor(COLORS["text"]))
        for s in range(0, 101, 10):
            y = self._speed_to_y(s)
            painter.drawText(QRectF(2, y - 8, pl - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{s}%")

        # X-axis labels (temp)
        for t in range(TEMP_MIN, self._temp_max + 1, 10):
            if t > self._temp_max:
                break
            x = self._temp_to_x(t)
            painter.drawText(QRectF(x - 16, pb + 4, 32, 18), Qt.AlignCenter, str(t))

        # Grid lines
        grid_pen = QPen(QColor("#444444"), 1)
        painter.setPen(grid_pen)
        for s in range(0, 101, 10):
            y = self._speed_to_y(s)
            painter.drawLine(int(pl), int(y), int(pr), int(y))
        for t in range(TEMP_MIN, self._temp_max + 1, 10):
            if t > self._temp_max:
                break
            x = self._temp_to_x(t)
            painter.drawLine(int(x), int(pt), int(x), int(pb))

        # Plot boundary
        boundary_pen = QPen(QColor("#444444"), 1)
        painter.setPen(boundary_pen)
        painter.drawRect(QRectF(pl, pt, pw, ph))

        for i in range(1, len(self._points)):
            left, right = self._points[i - 1], self._points[i]
            x1 = self._temp_to_x(left.temp)
            y1 = self._speed_to_y(left.speed)
            x2 = self._temp_to_x(right.temp)
            y2 = self._speed_to_y(right.speed)
            line_pen = QPen(self._accent, 3)
            line_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(line_pen)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Points
        for i, point in enumerate(self._points):
            px = self._temp_to_x(point.temp)
            py = self._speed_to_y(point.speed)
            if i == self._selected_point:
                painter.setBrush(QColor("#ffffff"))
                painter.setPen(QPen(self._accent, 2))
            else:
                painter.setBrush(self._accent)
                painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(px, py), POINT_R, POINT_R)

        # Hover tooltip
        if 0 <= self._hovered_index < len(self._points):
            point = self._points[self._hovered_index]
            px = self._temp_to_x(point.temp)
            py = self._speed_to_y(point.speed)
            label = f"{point.temp}\u00B0C @ {point.speed}%"
            tip_w = len(label) * 7 + 12
            tip_h = 22
            tip_x = min(px + 14, pr - tip_w)
            tip_y = max(py - 28, pt)
            painter.setBrush(QColor("#181818"))
            painter.setPen(QPen(QColor("#555555"), 1))
            painter.setOpacity(0.95)
            painter.drawRoundedRect(QRectF(tip_x, tip_y, tip_w, tip_h), 4, 4)
            painter.setOpacity(1.0)
            painter.setPen(QColor(COLORS["text"]))
            tip_font = QFont()
            tip_font.setPointSize(9)
            tip_font.setFamily("monospace")
            painter.setFont(tip_font)
            painter.drawText(QRectF(tip_x + 6, tip_y + 2, tip_w - 12, tip_h - 4),
                             Qt.AlignVCenter, label)

        painter.end()

    # ── Mouse interaction ──

    def mousePressEvent(self, event: QMouseEvent):
        mx, my = event.position().x(), event.position().y()

        if event.button() == Qt.LeftButton:
            idx = self._find_nearby_point(mx, my)
            if idx >= 0:
                self._selected_point = idx
                self._dragging = True
                self.point_selected.emit(idx)
            else:
                temp = self._x_to_temp(mx)
                speed = self._y_to_speed(my)
                self.point_added.emit(temp, speed)
                self._selected_point = -1
            self.update()

        elif event.button() == Qt.RightButton:
            idx = self._find_nearby_point(mx, my)
            if idx > 0 and idx < len(self._points) - 1:
                self.point_deleted.emit(idx)
                self._selected_point = -1
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        mx, my = event.position().x(), event.position().y()
        if self._dragging and self._selected_point >= 0:
            temp = self._x_to_temp(mx)
            speed = self._y_to_speed(my)
            self.point_moved.emit(self._selected_point, temp, speed)
            self.update()
        else:
            self._hovered_index = self._find_nearby_point(mx, my)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def leaveEvent(self, event):
        self._hovered_index = -1
        self.update()

    def keyPressEvent(self, event):
        """Allow Delete key to remove selected point."""
        if event.key() == Qt.Key_Delete and self._selected_point >= 0:
            idx = self._selected_point
            if 0 < idx < len(self._points) - 1:
                self.point_deleted.emit(idx)
                self._selected_point = -1
                self.update()
