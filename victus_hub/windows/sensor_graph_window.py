"""Standalone sensor graph window with live line chart and scale controls."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QHideEvent, QShowEvent

from victus_hub.widgets.sensor_line_graph import SensorLineGraph
from victus_hub.app.theme import COLORS
from victus_hub import api
from victus_hub.features.sensors.definitions import (
    SensorDefinition, sensor_definition_for_key,
)

SAMPLE_CAPACITY = 500
POLL_INTERVAL_MS = 1000

# Accent colors for different sensor types
GROUP_ACCENTS = {
    "CPU": QColor("#f04b4b"),   # red
    "GPU": QColor("#3aaeef"),   # blue
    "HP Embedded Controller": QColor("#06b48a"),  # green
    "System": QColor("#9d9d9d"),
}


class SensorGraphWindow(QMainWindow):
    """Standalone window showing a live line chart for one sensor."""

    def __init__(self, sensor_key: str, parent=None):
        super().__init__(parent)
        self._sensor_key = sensor_key
        self._definition: SensorDefinition = sensor_definition_for_key(sensor_key)
        self._started = False
        self._ram_max_set = False

        self.setWindowTitle(f"{self._definition.name} Graph")
        self.resize(820, 260)
        self.setMinimumSize(500, 200)
        # App icon (logoV) — same as main window
        from victus_hub.app.icon_utils import load_icon
        self.setWindowIcon(load_icon("logoV.png", size=48))

        self.setStyleSheet(f"background-color: {COLORS['bg']};")

        # Accent color
        accent = GROUP_ACCENTS.get(self._definition.group, QColor("#f04b4b"))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        # ── Title bar ──
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 2, 4, 2)
        title_row.setSpacing(8)

        name_label = QLabel(self._definition.name)
        name_label.setStyleSheet(f"color: #cfcfcf; font-size: 11px;")
        title_row.addWidget(name_label)

        self._current_label = QLabel("--")
        self._current_label.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: 800;")
        title_row.addWidget(self._current_label)

        self._source_label = QLabel("")
        self._source_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        title_row.addWidget(self._source_label)

        title_row.addStretch()
        root.addLayout(title_row)

        # ── Graph + scale bar ──
        content_row = QHBoxLayout()
        content_row.setSpacing(0)

        self._graph = SensorLineGraph(SAMPLE_CAPACITY)
        self._graph.set_value_range(
            float(self._definition.value_min),
            float(self._definition.value_max),
        )
        self._graph.set_unit(self._definition.unit)
        self._graph.set_accent(accent)
        content_row.addWidget(self._graph, 1)

        # Scale bar
        scale = QWidget()
        scale.setFixedWidth(60)
        scale.setStyleSheet("background-color: #3d3d3d;")
        scale_layout = QVBoxLayout(scale)
        scale_layout.setContentsMargins(2, 2, 2, 2)
        scale_layout.setSpacing(0)

        self._max_input = QLineEdit(str(self._definition.value_max))
        self._max_input.setFixedHeight(22)
        self._max_input.setStyleSheet(
            "background: #303030; border: 1px solid #555; color: #ffffff; font-size: 11px; padding-left: 4px;"
        )
        self._max_input.setAlignment(Qt.AlignCenter)
        self._max_input.editingFinished.connect(self._on_scale_changed)
        scale_layout.addWidget(self._max_input)

        scale_layout.addStretch()

        self._min_input = QLineEdit(str(self._definition.value_min))
        self._min_input.setFixedHeight(22)
        self._min_input.setStyleSheet(
            "background: #303030; border: 1px solid #555; color: #ffffff; font-size: 11px; padding-left: 4px;"
        )
        self._min_input.setAlignment(Qt.AlignCenter)
        self._min_input.editingFinished.connect(self._on_scale_changed)
        scale_layout.addWidget(self._min_input)

        content_row.addWidget(scale)
        root.addLayout(content_row, 1)

        # ── Poll timer ──
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        self._started = True

        # Initial poll
        self._poll()

    def _poll(self):
        """Read sensor and append to history."""
        try:
            snap = api.read_sensors()
        except Exception:
            return

        # Re-resolve definition in case extra sensors appeared
        self._definition = sensor_definition_for_key(self._sensor_key, snap)
        reading = self._definition.reading(snap)
        value = self._definition.numeric_value(snap)

        # RAM: set upper bound to system total RAM (once, so user can override)
        if self._sensor_key == "ram-usage" and not self._ram_max_set and snap.ram_total_gb is not None:
            self._ram_max_set = True
            vmax = float(snap.ram_total_gb)
            self._graph.set_value_range(0.0, vmax)
            self._max_input.setText(f"{vmax:.1f}")

        self._current_label.setText(reading.value)
        self._source_label.setText(getattr(reading, "source", ""))
        self._graph.append_sample(value)

    def _on_scale_changed(self):
        """Read min/max inputs and update graph range."""
        try:
            vmin = float(self._min_input.text())
            vmax = float(self._max_input.text())
            if vmax > vmin:
                self._graph.set_value_range(vmin, vmax)
        except ValueError:
            pass

    def closeEvent(self, event):
        self._poll_timer.stop()
        self._started = False
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        if self._started:
            self._poll_timer.start()
            self._poll()

    def hideEvent(self, event: QHideEvent):
        super().hideEvent(event)
        self._poll_timer.stop()
