"""Storage card listing local disks with usage bars."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QSizePolicy, QWidget, QScrollArea,
)

from hp_helper.theme import COLORS
from hp_helper.backend.types import DiskUsage

_WARN_PCT = 90.0


class _DiskRow(QWidget):
    """One disk: name, usage bar (green / red >90%), used/total GB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._name = QLabel("—")
        self._name.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
        """)
        layout.addWidget(self._name)

        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.setSpacing(8)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)  # tenths of a percent
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        self._bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar_row.addWidget(self._bar, 1)

        self._usage = QLabel("— / — GB")
        self._usage.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
        """)
        self._usage.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._usage.setMinimumWidth(96)
        bar_row.addWidget(self._usage, 0)

        layout.addLayout(bar_row)

    def set_disk(self, disk: DiskUsage):
        self._name.setText(disk.name)
        pct = max(0.0, min(100.0, disk.usage_pct))
        self._bar.setValue(int(round(pct * 10)))
        color = COLORS["accent_red"] if pct >= _WARN_PCT else COLORS["accent_green"]
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {COLORS['surface_raised']};
                border: none;
                border-radius: 5px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 5px;
            }}
        """)
        self._usage.setText(f"{disk.used_gb:.0f}/{disk.total_gb:.0f} GB")


class StorageCard(QFrame):
    """Card listing local storage disks with usage bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("storageCard")
        self.setMinimumSize(140, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(f"""
            #storageCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        title = QLabel("Storage")
        title.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """)
        layout.addWidget(title, 0, Qt.AlignLeft)

        # Scroll if many disks
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(12)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, 1)

        self._empty = QLabel("No disks found")
        self._empty.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 12px;
                background: transparent;
            }}
        """)
        self._empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._empty, 1)
        self._empty.hide()

        self._rows: list[_DiskRow] = []

    def update_disks(self, disks: list[DiskUsage]):
        """Refresh disk rows to match the given list."""
        if not disks:
            for row in self._rows:
                row.hide()
            self._scroll.hide()
            self._empty.show()
            return

        self._empty.hide()
        self._scroll.show()

        # Grow/shrink row pool
        while len(self._rows) < len(disks):
            row = _DiskRow()
            # Insert before the trailing stretch
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)
        while len(self._rows) > len(disks):
            row = self._rows.pop()
            self._list_layout.removeWidget(row)
            row.deleteLater()

        for row, disk in zip(self._rows, disks):
            row.set_disk(disk)
            row.show()
