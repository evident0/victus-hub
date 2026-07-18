"""Pill-shaped segmented control for mode selection."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont

from hp_helper.app.icon_utils import load_icon
from hp_helper.app.theme import COLORS

# Inner pill height (selected background). Outer frame adds 4px margin each side.
_SEG_H = 32
_FRAME_PAD = 4


class SegmentedControl(QFrame):
    """Rounded segmented control with selectable segments.

    Each segment can optionally carry an action icon (e.g. a cog button)
    that emits ``action_requested`` when clicked, independent of segment
    selection.
    """

    segment_selected = Signal(str)
    action_requested = Signal(str)

    def __init__(self, segments: list[tuple[str, str, bool]], parent=None):
        # segments: [(key, label, has_action), ...]
        super().__init__(parent)
        self._segment_buttons: dict[str, QPushButton] = {}
        self._selected_key: str | None = None

        outer_h = _SEG_H + _FRAME_PAD * 2
        self.setFixedHeight(outer_h)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_FRAME_PAD, _FRAME_PAD, _FRAME_PAD, _FRAME_PAD)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignVCenter)

        for key, label, has_action in segments:
            layout.addWidget(self._create_segment(key, label, has_action), 1)

        self.setStyleSheet(self._stylesheet())

    def set_selected(self, key: str):
        """Programmatically select a segment without emitting a signal."""
        if key == self._selected_key:
            return
        self._selected_key = key
        self._refresh_styles()

    # ── Internal ──

    def _create_segment(self, key: str, label: str, has_action: bool) -> QWidget:
        w = QWidget()
        w.setObjectName("segment")
        w.setFixedHeight(_SEG_H)
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(2)
        l.setAlignment(Qt.AlignVCenter)

        btn = QPushButton(label)
        btn.setObjectName("segBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(_SEG_H)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Explicit centering so global QPushButton padding does not drop the label
        btn.setFont(QFont("", 11, QFont.Bold))
        btn.setStyleSheet("")  # rely on parent frame stylesheet + polish
        btn.clicked.connect(lambda _, k=key: self._select(k))
        l.addWidget(btn, 1)
        self._segment_buttons[key] = btn

        if has_action:
            cog = QPushButton()
            cog.setObjectName("cogBtn")
            cog.setIcon(load_icon("icons8-settings-32.png", color="#9d9d9d", size=14))
            cog.setIconSize(QSize(14, 14))
            cog.setFixedSize(26, 26)
            cog.setCursor(Qt.PointingHandCursor)
            cog.setToolTip("Open fan curves editor")
            cog.clicked.connect(lambda _, k=key: self.action_requested.emit(k))
            l.addWidget(cog, 0, Qt.AlignVCenter)

        return w

    def _select(self, key: str):
        if key == self._selected_key:
            return
        self._selected_key = key
        self._refresh_styles()
        self.segment_selected.emit(key)

    def _refresh_styles(self):
        for key, btn in self._segment_buttons.items():
            btn.setProperty("selected", key == self._selected_key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _stylesheet(self) -> str:
        return f"""
            SegmentedControl {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {(_SEG_H + _FRAME_PAD * 2) // 2}px;
            }}
            #segment {{
                background: transparent;
                border: none;
            }}
            #segBtn {{
                background: transparent;
                border: none;
                border-radius: {_SEG_H // 2}px;
                /* Symmetric padding keeps label vertically centered */
                padding: 0px 14px;
                margin: 0px;
                color: {COLORS['text_secondary']};
                font-size: 11px;
                font-weight: 600;
            }}
            #segBtn:hover {{
                color: {COLORS['text']};
                background-color: rgba(255, 255, 255, 0.04);
            }}
            #segBtn[selected="true"] {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['accent_blue']};
            }}
            #cogBtn {{
                background: transparent;
                border: none;
                border-radius: 13px;
                padding: 0px;
                margin: 0px 4px 0px 0px;
            }}
            #cogBtn:hover {{
                background-color: {COLORS['surface_raised']};
            }}
        """
