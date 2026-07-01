"""Footer bar with version and hardware title."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from hp_helper.theme import COLORS


class Footer(QWidget):
    """Bottom bar showing version info and hardware title."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        self._version_label = QLabel("Version: 0.1.0")
        self._version_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; background: transparent;")
        layout.addWidget(self._version_label)

        layout.addStretch()

        self._title_label = QLabel("HP Laptop")
        self._title_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; background: transparent;")
        layout.addWidget(self._title_label)

    def set_hardware_title(self, title: str):
        """Update the hardware title displayed on the right."""
        self._title_label.setText(title)
