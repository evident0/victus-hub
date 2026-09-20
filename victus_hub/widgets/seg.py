"""Ohman segments: a filled accent pill, and a sliding underline of links."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from victus_hub.app.theme import COLORS, ui_font


class _Pill(QWidget):
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["accent"]))
        r = min(self.height() / 2, 8)
        p.drawRoundedRect(self.rect(), r, r)
        p.end()


class Seg(QWidget):
    """Labels in a sunken box; the accent pill slides to the selected one."""

    picked = Signal(int)

    def __init__(self, names: list[str], kind: str = "page", parent=None,
                 tips: list[str] | None = None):
        super().__init__(parent)
        self._labels: list[QLabel] = []
        self._enabled: list[bool] = []
        self._sel = -1
        self._kind = kind

        pad = 4 if kind == "page" else 3
        radius = 10 if kind == "page" else 9 if kind == "compact" else 8
        size = 13 if kind == "page" else 12
        pad_y = 9 if kind == "page" else 7 if kind == "compact" else 6
        pad_x = 12 if kind == "row" else 6

        self.setObjectName("seg")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._radius = radius
        self.setStyleSheet(
            f"#seg {{ background-color: {COLORS['sunken']}; border-radius: {radius}px; }}"
        )

        self._pill = _Pill(self)
        self._pill.hide()
        self._anim = QPropertyAnimation(self._pill, b"geometry", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        row = QHBoxLayout(self)
        row.setContentsMargins(pad, pad, pad, pad)
        row.setSpacing(0)

        for i, name in enumerate(names):
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignCenter)
            lab.setCursor(Qt.PointingHandCursor)
            lab.setFont(ui_font(size))
            lab.setStyleSheet(f"color: {COLORS['seg_text']}; background: transparent;")
            lab.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lab.setMinimumWidth(0)
            if tips and i < len(tips) and tips[i]:
                lab.setToolTip(tips[i])
            lab.installEventFilter(self)
            row.addWidget(lab, 1)
            self._labels.append(lab)
            self._enabled.append(True)

        self._font_size = size

    def eventFilter(self, obj, event):
        if obj in self._labels:
            idx = self._labels.index(obj)
            et = event.type()
            if et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                if idx != self._sel and self._enabled[idx]:
                    self.select(idx, True)
                    self.picked.emit(idx)
                return True
            if et == event.Type.Enter:
                if idx != self._sel and self._enabled[idx]:
                    obj.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
            elif et == event.Type.Leave:
                if idx != self._sel:
                    obj.setStyleSheet(f"color: {COLORS['seg_text']}; background: transparent;")
        return super().eventFilter(obj, event)

    def select(self, index: int, animate: bool = True) -> None:
        if index < 0 or index >= len(self._labels):
            return
        self._sel = index
        for i, lab in enumerate(self._labels):
            selected = i == index
            color = "#FFFFFF" if selected else COLORS["seg_text"]
            weight = "600" if selected else "400"
            lab.setStyleSheet(
                f"color: {color}; background: transparent; font-weight: {weight};"
            )
            font = ui_font(self._font_size, 600 if selected else 400)
            lab.setFont(font)
        self._place(animate)

    def selected(self) -> int:
        return self._sel

    def set_item_enabled(self, index: int, on: bool, why: str = "") -> None:
        if index < 0 or index >= len(self._labels):
            return
        self._enabled[index] = on
        self._labels[index].setEnabled(on)
        self._labels[index].setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self._labels[index].setToolTip(why if not on else "")

    def refresh_accent(self) -> None:
        self._pill.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place(False)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._place(False))

    def _place(self, animate: bool) -> None:
        if self._sel < 0:
            self._pill.hide()
            return
        lab = self._labels[self._sel]
        geo = lab.geometry()
        if geo.width() <= 0:
            return
        self._pill.show()
        self._pill.raise_()
        for lab in self._labels:
            lab.raise_()
        target = QRect(geo)
        if not animate or not self._pill.isVisible() or self._pill.width() <= 0:
            self._anim.stop()
            self._pill.setGeometry(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._pill.geometry())
        self._anim.setEndValue(target)
        self._anim.start()


class _Underline(QWidget):
    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["accent"]))
        p.end()


class LinkSeg(QWidget):
    """A row of text links; the accent underline slides to the selected one."""

    picked = Signal(int)

    def __init__(self, names: list[str], gap: int = 18, size: int = 14,
                 parent=None, wrap: bool = False):
        super().__init__(parent)
        self._labels: list[QLabel] = []
        self._off: list[bool] = []
        self._sel = -1
        self._gap = gap
        self._size = size
        self._wrap = wrap

        self._line = _Underline(self)
        self._line.setFixedHeight(1)
        self._line.hide()
        self._anim = QPropertyAnimation(self._line, b"geometry", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 4)
        row.setSpacing(gap)
        if wrap:
            row.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        for name in names:
            lab = QLabel(name)
            lab.setCursor(Qt.PointingHandCursor)
            lab.setFont(ui_font(size))
            lab.setStyleSheet(f"color: {COLORS['desc']}; background: transparent;")
            lab.installEventFilter(self)
            row.addWidget(lab)
            self._labels.append(lab)
            self._off.append(False)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def eventFilter(self, obj, event):
        if obj in self._labels:
            idx = self._labels.index(obj)
            et = event.type()
            if et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                if not self._off[idx]:
                    self.select(idx, True)
                    self.picked.emit(idx)
                return True
            if et == event.Type.Enter:
                if idx != self._sel and not self._off[idx]:
                    obj.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
            elif et == event.Type.Leave:
                if idx != self._sel:
                    obj.setStyleSheet(f"color: {COLORS['desc']}; background: transparent;")
        return super().eventFilter(obj, event)

    def select(self, index: int, animate: bool = True) -> None:
        if index < 0 or index >= len(self._labels):
            return
        self._sel = index
        for i, lab in enumerate(self._labels):
            color = COLORS["text_hi"] if i == index else COLORS["desc"]
            lab.setStyleSheet(f"color: {color}; background: transparent;")
        self._place(animate)

    def selected(self) -> int:
        return self._sel

    def set_text(self, index: int, text: str) -> None:
        if 0 <= index < len(self._labels):
            self._labels[index].setText(text)

    def set_item_enabled(self, index: int, on: bool, why: str = "") -> None:
        if index < 0 or index >= len(self._labels):
            return
        self._off[index] = not on
        self._labels[index].setEnabled(on)
        self._labels[index].setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self._labels[index].setToolTip(why if not on else "")
        self._labels[index].setStyleSheet(
            f"color: {COLORS['desc']}; background: transparent; "
            f"opacity: {'1' if on else '0.35'};"
        )

    def refresh_accent(self) -> None:
        self._line.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place(False)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._place(False))

    def _place(self, animate: bool) -> None:
        if self._sel < 0:
            self._line.hide()
            return
        lab = self._labels[self._sel]
        geo = lab.geometry()
        if geo.width() <= 0:
            return
        target = QRect(geo.x(), self.height() - 1, geo.width(), 1)
        self._line.show()
        if not animate or self._line.width() <= 0:
            self._anim.stop()
            self._line.setGeometry(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._line.geometry())
        self._anim.setEndValue(target)
        self._anim.start()
