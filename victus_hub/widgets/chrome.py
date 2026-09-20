"""Shared Ohman page chrome: titles, hairlines, metric tiles, settings rows."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from victus_hub.app.theme import COLORS, MONO_FONT, UI_FONT, mono_font, ui_font


def hairline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {COLORS['line']}; border: none;")
    return line


def section_label(text: str) -> QLabel:
    """Letter-spaced mono section heading used in Settings."""
    spaced = "  ".join(text)
    lab = QLabel(spaced)
    lab.setFont(mono_font(10, 500))
    lab.setStyleSheet(f"color: {COLORS['section']}; background: transparent;")
    return lab


class PageHead(QWidget):
    """Title on the left, mono status on the right."""

    def __init__(self, title: str = "", parent=None, *, status_width: int = 200):
        super().__init__(parent)
        self._status_width = status_width
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._title = QLabel(title)
        self._title.setFont(ui_font(15, 600))
        self._title.setStyleSheet(
            f"color: {COLORS['text']}; background: transparent; "
            f"font-size: 15px; font-weight: 600;"
        )
        row.addWidget(self._title)
        row.addStretch()
        self._status = QLabel("")
        self._status.setFont(mono_font(11))
        self._status.setStyleSheet(
            f"color: {COLORS['status']}; background: transparent; "
            f"font-size: 11px; font-family: '{MONO_FONT}';"
        )
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status.setMaximumWidth(status_width)
        row.addWidget(self._status)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_status(self, text: str) -> None:
        fm = self._status.fontMetrics()
        self._status.setText(fm.elidedText(text, Qt.ElideLeft, self._status_width))


class BigMetric(QWidget):
    """42 px reading with a small unit and a caption, like Ohman's home tiles."""

    def __init__(self, caption: str, unit: str = "", parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._value = QLabel("—")
        self._value.setFont(ui_font(42, 600))
        self._value.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._value.setStyleSheet(
            f"color: {COLORS['text']}; background: transparent; "
            f"font-size: 42px; font-weight: 600; font-family: '{UI_FONT}';"
        )

        self._unit = QLabel(unit)
        self._unit.setFont(ui_font(unit_size(unit), 400))
        self._unit.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._unit.setStyleSheet(
            f"color: {COLORS['sub']}; background: transparent; "
            f"font-size: {unit_size(unit)}px; font-family: '{UI_FONT}';"
        )

        num = QHBoxLayout()
        num.setContentsMargins(0, 0, 0, 0)
        num.setSpacing(0)
        num.addWidget(self._value, 0, Qt.AlignTop)
        num.addWidget(self._unit, 0, Qt.AlignTop)
        num.addStretch()
        col.addLayout(num)
        self._sync_unit_baseline()

        self._caption = QLabel(caption)
        self._caption.setFont(ui_font(12))
        self._caption.setStyleSheet(
            f"color: {COLORS['sub']}; background: transparent; padding-top: 6px; "
            f"font-size: 12px; font-family: '{UI_FONT}';"
        )
        col.addWidget(self._caption)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_reading(self, value: str, caption: str | None = None, unit: str | None = None) -> None:
        self._value.setText(value)
        if caption is not None:
            self._caption.setText(caption)
        if unit is not None:
            self._unit.setText(unit)
            self._unit.setFont(ui_font(unit_size(unit), 400))
            self._unit.setStyleSheet(
                f"color: {COLORS['sub']}; background: transparent; "
                f"font-size: {unit_size(unit)}px; font-family: '{UI_FONT}';"
            )
        self._sync_unit_baseline()

    def _sync_unit_baseline(self) -> None:
        """Sit °C / rpm on the same baseline as the 42 px reading."""
        top = self._value.fontMetrics().ascent() - self._unit.fontMetrics().ascent()
        self._unit.setContentsMargins(4, max(0, top), 0, 0)


def unit_size(unit: str) -> int:
    return 15 if "rpm" in unit.lower() or "min" in unit.lower() else 18


class SettingsRow(QWidget):
    """Title + optional subtitle on the left, control on the right."""

    def __init__(self, title: str, subtitle: str = "", control: QWidget | None = None,
                 parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(18)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
        t = QLabel(title, self)
        t.setFont(ui_font(14))
        t.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        t.setWordWrap(True)
        text.addWidget(t)
        self._sub = QLabel(subtitle, self)
        self._sub.setFont(ui_font(12))
        self._sub.setStyleSheet(f"color: {COLORS['desc']}; background: transparent;")
        self._sub.setWordWrap(True)
        self._sub.setVisible(bool(subtitle))
        text.addWidget(self._sub)
        row.addLayout(text, 1)

        if control is not None:
            row.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)

    def set_subtitle(self, text: str) -> None:
        self._sub.setText(text)
        self._sub.setVisible(bool(text))


class FooterBar(QWidget):
    """Hairline, green heartbeat, mono left/right — Ohman's page foot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 24, 0, 0)
        col.setSpacing(14)
        col.addWidget(hairline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {COLORS['ok']}; background: transparent; font-size: 8px;")
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        self._left = QLabel("")
        self._left.setFont(mono_font(11))
        self._left.setStyleSheet(f"color: {COLORS['foot']}; background: transparent;")
        row.addWidget(self._left)
        row.addStretch()
        self._right = QLabel("")
        self._right.setFont(mono_font(11))
        self._right.setStyleSheet(f"color: {COLORS['foot']}; background: transparent;")
        row.addWidget(self._right)
        col.addLayout(row)

    def set_left(self, text: str) -> None:
        self._left.setText(text)

    def set_right(self, text: str) -> None:
        self._right.setText(text)
