"""Modern circular gauge widget with gradient arcs and glow effects."""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QRadialGradient, QBrush,
)

from hp_helper.theme import COLORS


class GaugeDial(QWidget):
    """Circular gauge with three concentric progress arcs.

    Outer arc  — temperature (°C), color shifts green→orange→red.
    Middle arc — usage (%), blue.
    Inner arc  — fan RPM, green.
    """

    def __init__(
        self,
        label: str,
        outer_max: int = 100,
        mid_max: int = 100,
        inner_max: int = 6000,
        parent=None,
    ):
        super().__init__(parent)
        self._label = label
        self._outer_value: float | None = None
        self._outer_max = outer_max
        self._mid_value: float | None = None
        self._mid_max = mid_max
        self._inner_value: float | None = None
        self._inner_max = inner_max
        self.setMinimumSize(180, 180)

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

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Subtle radial background for depth
        bg_r = min(w, h) * 0.46
        bg_grad = QRadialGradient(cx, cy, bg_r)
        bg_grad.setColorAt(0.0, QColor(255, 255, 255, 10))
        bg_grad.setColorAt(0.7, QColor(255, 255, 255, 3))
        bg_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(bg_grad))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy), bg_r, bg_r)

        # Arc geometry — 270° sweep starting at 135°
        base_r = min(w, h) * 0.38
        arcs = [
            (base_r, 11, self._temp_color(self._outer_value),
             self._outer_value, self._outer_max),
            (base_r * 0.74, 8, QColor(COLORS["accent_blue"]),
             self._mid_value, self._mid_max),
            (base_r * 0.52, 5, QColor(COLORS["accent_green"]),
             self._inner_value, self._inner_max),
        ]
        for r, width, color, value, vmax in arcs:
            self._draw_arc(painter, cx, cy, r, width, color, value, vmax)

        # ── Label (top) ──
        label_font = QFont()
        label_font.setPointSize(10)
        label_font.setBold(True)
        label_font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        painter.setFont(label_font)
        painter.setPen(QColor(COLORS["text_secondary"]))
        painter.drawText(
            QRectF(0, cy - base_r - 28, w, 20),
            Qt.AlignCenter, self._label.upper(),
        )

        # ── Center: temperature (large) ──
        main_font = QFont()
        main_font.setPointSize(28)
        main_font.setBold(True)
        painter.setFont(main_font)
        painter.setPen(self._temp_color(self._outer_value))
        main_text = f"{self._outer_value:.0f}\u00B0" if self._outer_value is not None else "\u2014"
        painter.drawText(QRectF(0, cy - 22, w, 36), Qt.AlignCenter, main_text)

        # ── Sub: RPM (below center) ──
        sub_font = QFont()
        sub_font.setPointSize(10)
        painter.setFont(sub_font)
        painter.setPen(QColor(COLORS["text_secondary"]))
        sub_text = f"{self._inner_value:.0f} RPM" if self._inner_value is not None else "\u2014 RPM"
        painter.drawText(QRectF(0, cy + 14, w, 18), Qt.AlignCenter, sub_text)

        # ── Usage % (bottom) ──
        usage_text = f"{self._mid_value:.0f}%" if self._mid_value is not None else "\u2014%"
        painter.drawText(QRectF(0, cy + 32, w, 16), Qt.AlignCenter, usage_text)

        painter.end()

    def _draw_arc(self, painter, cx, cy, r, width, color, value, vmax):
        """Draw a track arc and a glowing progress arc."""
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        start = 135 * 16
        sweep = 270 * 16

        # Track
        track_pen = QPen(QColor(COLORS["surface_raised"]), width)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, start, sweep)

        # Progress
        progress = 0.0
        if value is not None and vmax > 0:
            progress = min(max(value / vmax, 0.0), 1.0)
        if progress <= 0:
            return

        # Glow (wider, semi-transparent)
        glow_pen = QPen(QColor(color.red(), color.green(), color.blue(), 50), width + 6)
        glow_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(glow_pen)
        painter.drawArc(rect, start, int(-sweep * progress))

        # Main arc
        val_pen = QPen(color, width)
        val_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(val_pen)
        painter.drawArc(rect, start, int(-sweep * progress))

    @staticmethod
    def _temp_color(value: float | None) -> QColor:
        """Return color based on temperature thresholds."""
        if value is None:
            return QColor(COLORS["text"])
        if value >= 85:
            return QColor("#ff2020")
        if value >= 70:
            return QColor("#ff8c00")
        return QColor("#06b48a")
