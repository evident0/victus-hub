"""App-styled popup menu with rounded corners."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QStyleFactory

from victus_hub.app.theme import COLORS


class PopupMenu(QMenu):
    """QMenu that matches the rest of the UI instead of the native boxy panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("popupMenu")
        # Breeze paints a sharp rectangular panel; Fusion honors QSS radius.
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            fusion.setParent(self)
            self.setStyle(fusion)
        self.setWindowFlags(
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        surface = COLORS["surface"]
        raised = COLORS["surface_raised"]
        border = COLORS["border"]
        text = COLORS["text"]
        muted = COLORS["text_secondary"]
        self.setStyleSheet(f"""
            QMenu#popupMenu {{
                background-color: {surface};
                color: {text};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu#popupMenu::item {{
                background-color: transparent;
                color: {text};
                padding: 6px 14px;
                border-radius: 6px;
                margin: 1px 2px;
            }}
            QMenu#popupMenu::item:selected {{
                background-color: {raised};
            }}
            QMenu#popupMenu::item:disabled {{
                color: {muted};
            }}
            QMenu#popupMenu::item:selected:disabled {{
                background-color: transparent;
                color: {muted};
            }}
            QMenu#popupMenu::item:checked {{
                font-weight: 600;
            }}
            QMenu#popupMenu::separator {{
                height: 1px;
                background: {border};
                margin: 4px 8px;
            }}
        """)
