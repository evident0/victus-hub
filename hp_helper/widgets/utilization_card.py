"""Utilization card with circular progress indicator for CPU/GPU/RAM."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QFont, QColor
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget, QSizePolicy

from hp_helper.theme import COLORS


class _CircularProgress(QWidget):
    """Circular progress arc with centred percentage and label text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percentage: float = 0.0
        self._accent: str = COLORS["accent_blue"]
        self._label: str = "Utilization"
        self.setMinimumSize(90, 90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_value(self, percentage: float, accent: str, label: str = "Utilization"):
        self._percentage = max(0.0, min(100.0, percentage))
        self._accent = accent
        self._label = label
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        side = min(w, h)
        pen_width = max(3.0, side * 0.08)

        # Arc rect — square, centred in widget, inset for pen width
        margin = pen_width / 2.0 + 2
        arc_side = side - 2 * margin
        offset_x = (w - arc_side) / 2.0
        offset_y = (h - arc_side) / 2.0
        rect = QRectF(offset_x, offset_y, arc_side, arc_side)

        # Background arc (full circle)
        bg_pen = QPen(QColor(COLORS["surface_raised"]), pen_width, Qt.SolidLine, Qt.RoundCap)
        p.setPen(bg_pen)
        p.drawArc(rect, 90 * 16, -360 * 16)

        # Foreground arc (utilization)
        if self._percentage > 0:
            span = int(-360 * self._percentage / 100.0 * 16)
            fg_pen = QPen(QColor(self._accent), pen_width, Qt.SolidLine, Qt.RoundCap)
            p.setPen(fg_pen)
            p.drawArc(rect, 90 * 16, span)

        # Percentage text (centred in arc rect)
        pct_font = QFont("", max(10, int(side * 0.22)), QFont.Bold)
        p.setFont(pct_font)
        p.setPen(QColor(COLORS["text"]))
        pct_text = f"{int(self._percentage)}%"
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, pct_text)

        # Label text below percentage (still inside circle)
        lbl_font = QFont("", max(7, int(side * 0.09)))
        p.setFont(lbl_font)
        p.setPen(QColor(COLORS["text_secondary"]))
        center_y = rect.center().y()
        label_rect = QRectF(
            rect.left(),
            center_y + side * 0.04,
            rect.width(),
            rect.bottom() - center_y - side * 0.04,
        )
        p.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop,
                    f"{int(self._percentage)} {self._label}")

        p.end()


class UtilizationCard(QFrame):
    """Card showing a circular utilization gauge for a hardware component.

    Displays a title (CPU/GPU/RAM) top-left, a circular progress ring
    with percentage and label, and a subtitle (temperature or memory used).
    """

    def __init__(self, title: str, subtitle: str = "—",
                 accent: str = COLORS["accent_blue"], parent=None):
        super().__init__(parent)
        self.setObjectName("utilCard")
        self.setMinimumSize(130, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(f"""
            #utilCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(2)

        # Title label (top-left)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """)
        layout.addWidget(self._title_label, 0, Qt.AlignLeft)

        # Circular progress
        self._circular = _CircularProgress()
        layout.addWidget(self._circular, 1)

        # Subtitle label (below circle, centred)
        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
        """)
        self._subtitle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._subtitle_label, 0)

    def update_data(self, percentage: float, subtitle: str,
                     accent: str | None = None):
        """Update the card with new sensor data."""
        if accent is not None:
            self._circular.set_value(percentage, accent)
        else:
            self._circular.set_value(percentage, self._circular._accent)
        self._subtitle_label.setText(subtitle)
