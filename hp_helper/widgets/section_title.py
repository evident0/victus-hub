"""Section title row with optional icon, title, and meta text."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from hp_helper.app.theme import COLORS


class SectionTitle(QWidget):
    """Horizontal row: optional icon + bold title + spacer + optional meta text."""

    def __init__(self, icon: str | None, title: str, meta: str | None = None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if icon:
            icon_label = QLabel(icon)
            icon_font = QFont()
            icon_font.setPointSize(14)
            icon_font.setBold(True)
            icon_label.setFont(icon_font)
            icon_label.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
            icon_label.setFixedWidth(22)
            layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        layout.addWidget(title_label)

        layout.addStretch()

        if meta:
            meta_label = QLabel(meta)
            meta_label.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 11px;")
            layout.addWidget(meta_label)
