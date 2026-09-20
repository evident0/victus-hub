"""Ohman toggle: 40×22 track, 16 px knob, accent when on."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QPropertyAnimation, Property, QEasingCurve, QSize
from PySide6.QtGui import QPainter, QColor, QBrush

from victus_hub.app.theme import COLORS, ui_font


class ToggleSwitch(QWidget):
    """A modern toggle switch with a sliding knob.

    Emits ``toggled(bool)`` like QCheckBox.  Use ``isChecked()`` /
    ``setChecked()`` to read/write state.
    """

    toggled = Signal(bool)

    def __init__(self, text: str = "", parent=None, big: bool = True):
        super().__init__(parent)
        self._checked = False
        self._knob_pos = 0.0  # 0.0 = off (left), 1.0 = on (right)

        if big:
            self._track_w = 40
            self._track_h = 22
            self._knob_d = 16
        else:
            self._track_w = 38
            self._track_h = 21
            self._knob_d = 15
        self._knob_margin = 3
        self._knob_travel = self._track_w - self._knob_d - 2 * self._knob_margin

        self._knob_on = QColor("#FFFFFF")
        self._knob_off = QColor(COLORS["knob_off"])

        self._label = QLabel(text) if text else None
        if self._label:
            self._label.setFont(ui_font(13))
            self._label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._track = _SwitchTrack(self)
        self._track.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self._track)
        if self._label:
            layout.addWidget(self._label)
        self.setFixedSize(self.sizeHint())
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._anim = QPropertyAnimation(self, b"knob_pos")
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def sizeHint(self) -> QSize:
        w = self._track_w
        h = self._track_h
        if self._label:
            lw = self._label.sizeHint().width()
            lh = self._label.sizeHint().height()
            w += 10 + lw
            h = max(h, lh)
        return QSize(w, h)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked == checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.toggled.emit(checked)

    def _get_knob_pos(self) -> float:
        return self._knob_pos

    def _set_knob_pos(self, pos: float):
        self._knob_pos = pos
        for child in self.findChildren(_SwitchTrack):
            child.update()

    knob_pos = Property(float, _get_knob_pos, _set_knob_pos)

    def mousePressEvent(self, event):
        pos = event.position()
        if 0 <= pos.x() <= self._track_w and 0 <= pos.y() <= self._track_h:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)


class _SwitchTrack(QWidget):
    def __init__(self, switch: ToggleSwitch, parent=None):
        super().__init__(parent)
        self._switch = switch
        self.setFixedSize(switch._track_w, switch._track_h)

    def paintEvent(self, _event):
        s = self._switch
        w, h = s._track_w, s._track_h
        d = s._knob_d
        m = s._knob_margin
        travel = s._knob_travel

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if s._knob_pos >= 0.5:
            track = QColor(COLORS["accent"])
            knob = s._knob_on
        else:
            track = QColor(COLORS["switch_off"])
            knob = QColor(COLORS["knob_off"])
        p.setBrush(QBrush(track))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        knob_x = m + s._knob_pos * travel
        knob_y = (h - d) / 2
        p.setBrush(QBrush(knob))
        p.drawEllipse(QRectF(knob_x, knob_y, d, d))

        p.end()
