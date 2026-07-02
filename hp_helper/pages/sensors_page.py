"""Sensors page with collapsible grouped table."""

from PySide6.QtWidgets import (
    QAbstractItemView, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon

from hp_helper.theme import COLORS
from pathlib import Path

_ICONS_DIR = Path(__file__).parent.parent / "resources" / "icons"
_GRAPH_ICON = str(_ICONS_DIR / "icons8-show-right-side-panel-48-white.png")


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
        self._tree.setHeaderLabels(["Sensor", "Current", "Maximum", "Minimum", "Average", ""])
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
        self._tree.setColumnWidth(5, 48)

        self._tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._tree)

    def update_rows(self, rows: list):
        """Rebuild the tree from sensor table rows, preserving scroll + expand state."""
        # Save scroll position and expanded groups before clearing
        scroll_bar = self._tree.verticalScrollBar()
        saved_scroll = scroll_bar.value()
        saved_expanded: set[str] = set()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.isExpanded():
                saved_expanded.add(item.text(0))

        self._tree.clear()

        groups: dict[str, list] = {}
        for row in rows:
            g = row["definition"].group
            groups.setdefault(g, []).append(row)

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

            for ri, row in enumerate(group_rows):
                d = row["definition"]
                stats = row["stats"]

                item = QTreeWidgetItem(group_item)
                item.setText(0, d.name)
                item.setToolTip(0, stats.get("current", {}).get("source", ""))

                current_val = stats.get("current", {}).get("value", "—")
                max_val = str(stats.get("maximum", "—"))
                min_val = str(stats.get("minimum", "—"))
                avg_val = str(stats.get("average", "—"))

                item.setText(1, str(current_val))
                item.setText(2, max_val)
                item.setText(3, min_val)
                item.setText(4, avg_val)
                item.setText(5, "")

                # Graph button in column 5
                icon = QIcon(_GRAPH_ICON)
                btn = QPushButton()
                btn.setIcon(icon)
                btn.setIconSize(QSize(18, 18))
                btn.setFixedSize(22, 22)
                btn.setFlat(True)
                btn.setStyleSheet("QPushButton { border: none; background: transparent; }")
                if d.graphable:
                    btn.setToolTip(f"Open {d.name} graph")
                    btn.clicked.connect(
                        lambda checked, k=d.key: self.open_graph_requested.emit(k)
                    )
                else:
                    btn.setEnabled(False)
                    btn.setToolTip("Not graphable")
                container = QWidget()
                container.setStyleSheet("background: transparent;")
                clayout = QVBoxLayout(container)
                clayout.setContentsMargins(0, 0, 0, 0)
                clayout.setAlignment(Qt.AlignCenter)
                clayout.addWidget(btn)
                self._tree.setItemWidget(item, 5, container)

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

        # Restore scroll position after the tree repopulates; deferred to
        # the next event-loop tick so the scrollbar range is up to date.
        QTimer.singleShot(0, lambda: scroll_bar.setValue(saved_scroll))


    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        """Toggle collapse/expand on group header click (col 0)."""
        if item.parent() is None:  # group header
            item.setExpanded(not item.isExpanded())


