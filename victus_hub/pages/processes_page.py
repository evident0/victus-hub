"""Processes page — top processes table in its own panel."""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from victus_hub.widgets.top_processes_card import TopProcessesCard


class ProcessesPage(QWidget):
    """Tab hosting the top-processes card full-size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        self._top_processes = TopProcessesCard()
        layout.addWidget(self._top_processes, 1)

    def refresh(self) -> None:
        """Refresh the process list while this page is active."""
        self._top_processes.refresh()

    def set_active(self, active: bool) -> None:
        """Start or cancel process metric collection for this page."""
        self._top_processes.set_active(active)
