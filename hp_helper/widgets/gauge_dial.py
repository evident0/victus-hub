"""Gauge dial widget with three concentric arcs for temp/usage/RPM."""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics

from hp_helper.theme import COLORS


class GaugeDial(QWidget):
    """Custom widget drawing three concentric arcs with value labels."""

    def __init__(
        self,
        label: str,
        outer_value: float | None = None,
        outer_max: int = 100,
        outer_color: str = "#ff2020",
        mid_value: float | None = None,
        mid_max: int = 100,
        mid_color: str = "#3aaeef",
        inner_value: float | None = None,
        inner_max: int = 6000,
        inner_color: str = "#06b48a",
        parent=None,
    ):
        super().__init__(parent)
        self._label = label
        self._outer_value = outer_value
        self._outer_max = outer_max
        self._outer_color = QColor(outer_color)
        self._mid_value = mid_value
        self._mid_max = mid_max
        self._mid_color = QColor(mid_color)
        self._inner_value = inner_value
        self._inner_max = inner_max
        self._inner_color = QColor(inner_color)
        self.setMinimumSize(160, 160)

    def update_values(
        self,
        outer_value: float | None = None,
        mid_value: float | None = None,
        inner_value: float | None = None,
    ):
        """Update displayed values and trigger repaint."""
        self._outer_value = outer_value
        self._mid_value = mid_value
        self._inner_value = inner_value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h * 0.55

        # Size arcs relative to widget
        base_r = min(w, h) * 0.35
        outer_r = base_r
        mid_r = base_r * 0.70
        inner_r = base_r * 0.40

        # Draw arcs
        self._draw_arc(painter, cx, cy, outer_r, 10, self._outer_color, self._outer_value, self._outer_max)
        self._draw_arc(painter, cx, cy, mid_r, 8, self._mid_color, self._mid_value, self._mid_max)
        self._draw_arc(painter, cx, cy, inner_r, 6, self._inner_color, self._inner_value, self._inner_max)

        # Label at top center
        label_font = QFont()
        label_font.setPointSize(11)
        label_font.setBold(True)
        label_font.setCapitalization(QFont.AllUppercase)
        painter.setFont(label_font)
        painter.setPen(QColor(COLORS["text_secondary"]))
        label_rect = QRectF(0, cy - base_r - 30, w, 20)
        painter.drawText(label_rect, Qt.AlignCenter, self._label)

        # Center values
        # Main value (temp or speed)
        main_font = QFont()
        main_font.setPointSize(22)
        main_font.setBold(True)
        painter.setFont(main_font)

        if self._outer_value is not None:
            main_text = f"{self._outer_value:.0f}\u00B0C"
        else:
            main_text = "--\u00B0C"
        painter.setPen(QColor(self._temp_color(self._outer_value)))
        main_rect = QRectF(0, cy - 16, w, 30)
        painter.drawText(main_rect, Qt.AlignCenter, main_text)

        # Sub value (RPM or %)
        sub_font = QFont()
        sub_font.setPointSize(10)
        painter.setFont(sub_font)

        if self._inner_value is not None:
            sub_text = f"{self._inner_value:.0f} RPM"
        else:
            sub_text = "-- RPM"
        painter.setPen(QColor(COLORS["text_secondary"]))
        sub_rect = QRectF(0, cy + 10, w, 18)
        painter.drawText(sub_rect, Qt.AlignCenter, sub_text)

        painter.end()

    def _draw_arc(self, painter, cx, cy, r, width, color, value, vmax):
        """Draw a track arc and a filled progress arc."""
        # Track arc (background)
        track_pen = QPen(QColor(COLORS["surface_raised"]), width)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        # Draw 270° arc starting at 135° (top-left)
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        # QPainter.drawArc uses 1/16 degree units, starts at 3 o'clock, goes counter-clockwise
        # 135° = 3 o'clock counter-clockwise... actually in Qt, angles go counter-clockwise
        # 270° sweep = 270 * 16 = 4320
        painter.drawArc(rect, 135 * 16, 270 * 16)

        # Value arc
        progress = 0.0
        if value is not None and vmax > 0:
            progress = min(max(value / vmax, 0.0), 1.0)

        if progress > 0:
            val_pen = QPen(color, width)
            val_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(val_pen)
            painter.drawArc(rect, 135 * 16, int(-270 * progress * 16))

    @staticmethod
    def _temp_color(value: float | None) -> str:
        """Return color based on temperature thresholds."""
        if value is None:
            return COLORS["text"]
        if value >= 85:
            return "#ff2020"
        if value >= 70:
            return "#ff8c00"
        return COLORS["text"]
