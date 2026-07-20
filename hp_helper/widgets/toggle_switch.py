"""Modern toggle switch with sliding knob."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QPropertyAnimation, Property, QEasingCurve, QSize
from PySide6.QtGui import QPainter, QColor, QBrush


class ToggleSwitch(QWidget):
    """A modern toggle switch with a sliding knob.

    Emits ``toggled(bool)`` like QCheckBox.  Use ``isChecked()`` /
    ``setChecked()`` to read/write state.
    """

    toggled = Signal(bool)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._checked = False
        self._knob_pos = 0.0  # 0.0 = off (left), 1.0 = on (right)

        self._track_w = 40
        self._track_h = 22
        self._knob_d = 18
        self._knob_margin = 2
        self._knob_travel = self._track_w - self._knob_d - 2 * self._knob_margin

        self._track_off = QColor("#505050")
        self._track_on = QColor("#3aaeef")
        self._knob_color = QColor("#ffffff")

        self._label = QLabel(text) if text else None
        if self._label:
            self._label.setStyleSheet("color: #ffffff; font-size: 13px;")

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
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

    def sizeHint(self) -> QSize:
        w = self._track_w
        h = self._track_h
        if self._label:
            lw = self._label.sizeHint().width()
            lh = self._label.sizeHint().height()
            w += 10 + lw  # spacing + label
            h = max(h, lh)
        return QSize(w, h)

    # -- public API -------------------------------------------------

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

    # -- Qt property (drives the animation) -------------------------

    def _get_knob_pos(self) -> float:
        return self._knob_pos

    def _set_knob_pos(self, pos: float):
        self._knob_pos = pos
        for child in self.findChildren(_SwitchTrack):
            child.update()

    knob_pos = Property(float, _get_knob_pos, _set_knob_pos)

    # -- mouse ------------------------------------------------------

    def mousePressEvent(self, event):
        # Only toggle when the click lands on the track, not the label.
        pos = event.position()
        if 0 <= pos.x() <= self._track_w and 0 <= pos.y() <= self._track_h:
            self.setChecked(not self._checked)  # setChecked emits toggled
        super().mousePressEvent(event)


class _SwitchTrack(QWidget):
    """Internal widget that paints the track + knob."""

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

        # Track
        track_color = s._track_off if s._knob_pos < 0.5 else s._track_on
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        # Knob
        knob_x = m + s._knob_pos * travel
        knob_y = (h - d) / 2
        p.setBrush(QBrush(s._knob_color))
        p.drawEllipse(QRectF(knob_x, knob_y, d, d))

        p.end()
