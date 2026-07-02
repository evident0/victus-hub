"""Sensors page with collapsible grouped table."""

from PySide6.QtWidgets import (
    QAbstractItemView, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon

from hp_helper.theme import COLORS
from pathlib import Path

from hp_helper.icon_utils import load_pixmap

# Lazily built so QApplication exists before QPixmap allocation
_graph_icon: QIcon | None = None


def _get_graph_icon() -> QIcon:
    """Return the graph button icon, building it on first call."""
    global _graph_icon
    if _graph_icon is None:
        normal = load_pixmap("line_graph_icon-white.png", "#c0c0c0", 18)
        disabled = load_pixmap("line_graph_icon-white.png", "#2a2a2a", 18)
        _graph_icon = QIcon()
        _graph_icon.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
        _graph_icon.addPixmap(disabled, QIcon.Mode.Disabled, QIcon.State.Off)
    return _graph_icon


class SensorsPage(QWidget):
    """Tab displaying sensor data in a grouped tree table."""

    open_graph_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(6)
        self._tree.setHeaderLabels(["Sensor", "Current", "Maximum", "Minimum", "Average", "Graph"])
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(0)
        self._tree.setAlternatingRowColors(False)
        self._tree.setAnimated(True)
        self._tree.setSelectionMode(QAbstractItemView.NoSelection)

        # Column widths
        self._tree.setColumnWidth(0, 200)
        self._tree.setColumnWidth(1, 100)
        self._tree.setColumnWidth(2, 100)
        self._tree.setColumnWidth(3, 100)
        self._tree.setColumnWidth(4, 100)
        self._tree.setColumnWidth(5, 60)

        self._tree.itemClicked.connect(self._on_item_clicked)

        # Tracking for in-place updates (avoids destroying item widgets on every poll)
        self._sensor_items: dict[str, QTreeWidgetItem] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        layout.addWidget(self._tree)

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
                    gitem.setText(1, f"({len(group_rows)})")

            for key, row in by_key.items():
                item = self._sensor_items[key]
                d = row["definition"]
                stats = row["stats"]
                current_val = stats.get("current", {}).get("value", "—")
                item.setText(1, str(current_val))
                item.setText(2, str(stats.get("maximum", "—")))
                item.setText(3, str(stats.get("minimum", "—")))
                item.setText(4, str(stats.get("average", "—")))
                item.setToolTip(0, stats.get("current", {}).get("source", ""))
                # Temp coloring
                if d.unit == "\u00B0C":
                    try:
                        n = float(str(current_val).replace("\u00B0C", "").strip())
                        item.setForeground(1, QColor("#ff4444") if n > 95 else QColor(COLORS["text"]))
                    except ValueError:
                        pass
                else:
                    item.setForeground(1, QColor(COLORS["text"]))
            return

        # ── Structure changed: rebuild the tree ──
        # Save scroll position and expanded groups before clearing
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
            group_item.setText(0, group_name)
            group_item.setText(1, f"({len(group_rows)})")
            group_item.setExpanded(group_name in saved_expanded if saved_expanded else True)
            # Style group header
            for c in range(6):
                group_item.setBackground(c, QColor("#252525"))
            group_font = group_item.font(0)
            group_font.setBold(True)
            group_font.setPointSize(10)
            group_item.setFont(0, group_font)
            self._group_items[group_name] = group_item

            for ri, row in enumerate(group_rows):
                d = row["definition"]
                stats = row["stats"]

                item = QTreeWidgetItem(group_item)
                item.setText(0, d.name)
                item.setToolTip(0, stats.get("current", {}).get("source", ""))

                current_val = stats.get("current", {}).get("value", "—")
                item.setText(1, str(current_val))
                item.setText(2, str(stats.get("maximum", "—")))
                item.setText(3, str(stats.get("minimum", "—")))
                item.setText(4, str(stats.get("average", "—")))

                # Graph button in column 5 — transparent icon-only
                btn = QPushButton()
                btn.setIcon(_get_graph_icon())
                btn.setIconSize(QSize(18, 18))
                btn.setFixedSize(24, 24)
                btn.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        border: none;
                        border-radius: 3px;
                    }
                    QPushButton:hover {
                        background: rgba(255, 255, 255, 0.10);
                    }
                    QPushButton:pressed {
                        background: rgba(255, 255, 255, 0.15);
                    }
                    QPushButton:disabled {
                        background: transparent;
                    }
                """)
                if d.graphable:
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setToolTip(f"Open {d.name} graph")
                    btn.clicked.connect(
                        lambda checked, k=d.key: self.open_graph_requested.emit(k)
                    )
                else:
                    btn.setEnabled(False)
                    btn.setToolTip("Not graphable")
                self._tree.setItemWidget(item, 5, btn)
                # Alternating row color
                bg = "#181818" if ri % 2 == 0 else "#1d1d1d"
                for c in range(6):
                    item.setBackground(c, QColor(bg))

                # Temp coloring
                if d.unit == "\u00B0C":
                    try:
                        n = float(str(current_val).replace("\u00B0C", "").strip())
                        if n > 95:
                            item.setForeground(1, QColor("#ff4444"))
                    except ValueError:
                        pass

                self._sensor_items[d.key] = item

        # Restore scroll position after the tree repopulates
        QTimer.singleShot(0, lambda: scroll_bar.setValue(saved_scroll))


    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        """Toggle collapse/expand on group header click (col 0)."""
        if item.parent() is None:  # group header
            item.setExpanded(not item.isExpanded())


