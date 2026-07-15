"""Sensors page with collapsible grouped table (Top Processes visual style)."""

from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon

from hp_helper.app.theme import COLORS
from hp_helper.app.icon_utils import load_pixmap

# Lazily built so QApplication exists before QPixmap allocation
_graph_icon: QIcon | None = None

_COL_SENSOR = 0
_COL_CURRENT = 1
_COL_MAX = 2
_COL_MIN = 3
_COL_AVG = 4
_COL_GRAPH = 5

_GRAPH_BTN_STYLE = f"""
    QPushButton {{
        background: transparent;
        border: none;
        border-radius: 3px;
    }}
    QPushButton:hover {{
        background-color: {COLORS['surface_raised']};
    }}
    QPushButton:pressed {{
        background-color: {COLORS['border']};
    }}
    QPushButton:disabled {{
        background: transparent;
    }}
"""


def _get_graph_icon() -> QIcon:
    """Return the graph button icon, building it on first call."""
    global _graph_icon
    if _graph_icon is None:
        pm = load_pixmap("sensor_graph_button.png", None, 18)
        _graph_icon = QIcon(pm)
    return _graph_icon


class SensorsPage(QWidget):
    """Tab displaying sensor data in a grouped tree table."""

    open_graph_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        surface = COLORS["surface"]
        raised = COLORS["surface_raised"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        # Card frame — same shell as Top Processes
        card = QFrame()
        card.setObjectName("sensorsCard")
        card.setStyleSheet(f"""
            #sensorsCard {{
                background-color: {surface};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(0)

        self._tree = QTreeWidget()
        self._tree.setObjectName("sensorsTree")
        self._tree.setColumnCount(6)
        self._tree.setHeaderLabels([
            "Sensor", "Current", "Maximum", "Minimum", "Average", "Graph",
        ])
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tree.setFocusPolicy(Qt.StrongFocus)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Expand/collapse only via the branch arrow (match Top Processes)
        self._tree.setExpandsOnDoubleClick(False)

        self._tree.setStyleSheet(f"""
            QTreeWidget#sensorsTree {{
                background-color: {surface};
                border: none;
                border-radius: 0;
                outline: none;
                color: {COLORS['text']};
            }}
            QTreeWidget#sensorsTree::item {{
                padding: 3px 6px;
                background-color: {surface};
            }}
            QTreeWidget#sensorsTree::item:hover {{
                background-color: {raised};
            }}
            QTreeWidget#sensorsTree::item:selected,
            QTreeWidget#sensorsTree::item:selected:active {{
                background-color: {raised};
                color: {COLORS['text']};
            }}
            QHeaderView::section {{
                background-color: {surface};
                color: {COLORS['text_secondary']};
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                padding: 4px 8px;
                font-weight: bold;
                font-size: 11px;
            }}
            QHeaderView {{
                background-color: {surface};
            }}
        """)

        header = self._tree.header()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_COL_SENSOR, QHeaderView.Stretch)
        for col in (_COL_CURRENT, _COL_MAX, _COL_MIN, _COL_AVG):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self._tree.setColumnWidth(col, 100)
            self._tree.headerItem().setTextAlignment(
                col, int(Qt.AlignRight | Qt.AlignVCenter),
            )
        header.setSectionResizeMode(_COL_GRAPH, QHeaderView.Fixed)
        self._tree.setColumnWidth(_COL_GRAPH, 56)
        self._tree.headerItem().setTextAlignment(
            _COL_GRAPH, int(Qt.AlignHCenter | Qt.AlignVCenter),
        )

        card_layout.addWidget(self._tree)
        layout.addWidget(card)

        # Tracking for in-place updates (avoids destroying item widgets on every poll)
        self._sensor_items: dict[str, QTreeWidgetItem] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}

    def update_rows(self, rows: list):
        """Refresh sensor values in-place when structure is stable; rebuild only on change."""
        by_key: dict[str, dict] = {}
        groups: dict[str, list] = {}
        for row in rows:
            k = row["definition"].key
            by_key[k] = row
            g = row["definition"].group
            groups.setdefault(g, []).append(row)

        new_keys = set(by_key.keys())
        existing_keys = set(self._sensor_items.keys())

        if new_keys == existing_keys and self._group_items:
            # ── Structure unchanged: update text in-place ──
            for group_name, group_rows in groups.items():
                gitem = self._group_items.get(group_name)
                if gitem is not None:
                    gitem.setText(_COL_CURRENT, f"({len(group_rows)})")

            for key, row in by_key.items():
                item = self._sensor_items[key]
                d = row["definition"]
                stats = row["stats"]
                current_val = stats.get("current", {}).get("value", "—")
                item.setText(_COL_CURRENT, str(current_val))
                item.setText(_COL_MAX, str(stats.get("maximum", "—")))
                item.setText(_COL_MIN, str(stats.get("minimum", "—")))
                item.setText(_COL_AVG, str(stats.get("average", "—")))
                item.setToolTip(_COL_SENSOR, stats.get("current", {}).get("source", ""))
                # Temp coloring (value only — row stays flat)
                if d.unit == "\u00B0C":
                    try:
                        n = float(str(current_val).replace("\u00B0C", "").strip())
                        item.setForeground(
                            _COL_CURRENT,
                            QColor("#ff4444") if n > 95 else QColor(COLORS["text"]),
                        )
                    except ValueError:
                        pass
                else:
                    item.setForeground(_COL_CURRENT, QColor(COLORS["text"]))
            return

        # ── Structure changed: rebuild the tree ──
        scroll_bar = self._tree.verticalScrollBar()
        saved_scroll = scroll_bar.value()
        saved_expanded: set[str] = set()
        for i in range(self._tree.topLevelItemCount()):
            gitem = self._tree.topLevelItem(i)
            if gitem.isExpanded():
                saved_expanded.add(gitem.text(0))

        self._tree.clear()
        self._sensor_items.clear()
        self._group_items.clear()

        for group_name, group_rows in groups.items():
            group_item = QTreeWidgetItem(self._tree)
            group_item.setText(_COL_SENSOR, group_name)
            group_item.setText(_COL_CURRENT, f"({len(group_rows)})")
            group_item.setExpanded(
                group_name in saved_expanded if saved_expanded else True
            )
            group_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            group_font = group_item.font(_COL_SENSOR)
            group_font.setBold(True)
            group_item.setFont(_COL_SENSOR, group_font)
            self._group_items[group_name] = group_item

            for row in group_rows:
                d = row["definition"]
                stats = row["stats"]

                item = QTreeWidgetItem(group_item)
                item.setText(_COL_SENSOR, d.name)
                item.setToolTip(_COL_SENSOR, stats.get("current", {}).get("source", ""))

                current_val = stats.get("current", {}).get("value", "—")
                item.setText(_COL_CURRENT, str(current_val))
                item.setText(_COL_MAX, str(stats.get("maximum", "—")))
                item.setText(_COL_MIN, str(stats.get("minimum", "—")))
                item.setText(_COL_AVG, str(stats.get("average", "—")))
                for col in (_COL_CURRENT, _COL_MAX, _COL_MIN, _COL_AVG):
                    item.setTextAlignment(col, int(Qt.AlignRight | Qt.AlignVCenter))

                # Graph button — icon-only, flat
                btn = QPushButton()
                btn.setIcon(_get_graph_icon())
                btn.setIconSize(QSize(18, 18))
                btn.setFixedSize(24, 24)
                btn.setStyleSheet(_GRAPH_BTN_STYLE)
                if d.graphable:
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setToolTip(f"Open {d.name} graph")
                    btn.clicked.connect(
                        lambda checked=False, k=d.key: self.open_graph_requested.emit(k)
                    )
                else:
                    btn.setEnabled(False)
                    btn.setToolTip("Not graphable")
                self._tree.setItemWidget(item, _COL_GRAPH, btn)

                # Temp coloring
                if d.unit == "\u00B0C":
                    try:
                        n = float(str(current_val).replace("\u00B0C", "").strip())
                        if n > 95:
                            item.setForeground(_COL_CURRENT, QColor("#ff4444"))
                    except ValueError:
                        pass

                self._sensor_items[d.key] = item

        # Align metric columns on group rows too
        for gitem in self._group_items.values():
            gitem.setTextAlignment(
                _COL_CURRENT, int(Qt.AlignRight | Qt.AlignVCenter),
            )

        QTimer.singleShot(0, lambda: scroll_bar.setValue(saved_scroll))
