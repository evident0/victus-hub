"""Pill-shaped segmented control for mode selection."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont

from victus_hub.app.icon_utils import load_icon
from victus_hub.app.theme import COLORS


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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

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
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(4)

        btn = QPushButton(label)
        btn.setObjectName("segBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("", 11, QFont.Bold))
        btn.clicked.connect(lambda _, k=key: self._select(k))
        l.addWidget(btn, 1)
        self._segment_buttons[key] = btn

        if has_action:
            cog = QPushButton()
            cog.setObjectName("cogBtn")
            cog.setIcon(load_icon("NewIcons/settings.png", size=20))
            cog.setIconSize(QSize(20, 20))
            cog.setFixedSize(28, 28)
            cog.setCursor(Qt.PointingHandCursor)
            cog.setToolTip("Open fan curves editor")
            cog.clicked.connect(lambda _, k=key: self.action_requested.emit(k))
            l.addWidget(cog)

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
                border-radius: 18px;
            }}
            #segment {{
                background: transparent;
                border: none;
            }}
            #segBtn {{
                background: transparent;
                border: none;
                border-radius: 14px;
                padding: 8px 12px;
                color: {COLORS['text_secondary']};
            }}
            #segBtn:hover {{
                color: {COLORS['text']};
            }}
            #segBtn[selected="true"] {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['accent_blue']};
            }}
            #cogBtn {{
                background: transparent;
                border: none;
                border-radius: 14px;
            }}
            #cogBtn:hover {{
                background-color: {COLORS['surface_raised']};
            }}
        """
