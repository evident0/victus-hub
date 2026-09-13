"""App-styled combo box with a rounded popup and Lucide chevron."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleOptionViewItem,
)

from victus_hub.app.theme import COLORS

_ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_CHEVRON_DOWN = _ICON_DIR / "chevron-down.png"
_CHEVRON_UP = _ICON_DIR / "chevron-up.png"
_POPUP_RADIUS = 8


def _fusion_style(owner):
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        fusion.setParent(owner)
    return fusion


def _dark_palette() -> QPalette:
    pal = QPalette()
    bg = QColor(COLORS["surface"])
    fg = QColor(COLORS["text"])
    raised = QColor(COLORS["surface_raised"])
    muted = QColor(COLORS["text_secondary"])
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        pal.setColor(group, QPalette.ColorRole.Window, bg)
        pal.setColor(group, QPalette.ColorRole.Base, bg)
        pal.setColor(group, QPalette.ColorRole.AlternateBase, raised)
        pal.setColor(group, QPalette.ColorRole.Text, fg)
        pal.setColor(group, QPalette.ColorRole.WindowText, fg)
        pal.setColor(group, QPalette.ColorRole.Button, raised)
        pal.setColor(group, QPalette.ColorRole.ButtonText, fg)
        pal.setColor(group, QPalette.ColorRole.Highlight, raised)
        pal.setColor(group, QPalette.ColorRole.HighlightedText, fg)
        pal.setColor(group, QPalette.ColorRole.PlaceholderText, muted)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted)
    return pal


class _RoundedItemDelegate(QStyledItemDelegate):
    """Paint combo rows with the same rounded highlight as PopupMenu."""

    _radius = 6

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = opt.rect.adjusted(2, 1, -2, -1)
        selected = bool(opt.state & QStyle.State_Selected)
        hovered = bool(opt.state & QStyle.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["surface_raised"]))
            painter.drawRoundedRect(rect, self._radius, self._radius)

        disabled = not bool(opt.state & QStyle.State_Enabled)
        painter.setPen(QColor(
            COLORS["text_secondary"] if disabled else COLORS["text"]
        ))
        text = index.data(Qt.DisplayRole) or ""
        painter.drawText(
            rect.adjusted(12, 0, -8, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            str(text),
        )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 28))
        return size


class _PopupChromeFilter(QObject):
    """Paint a rounded opaque panel; skip QFrame's rectangular frame."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint:
            painter = QPainter(obj)
            if not painter.isActive():
                return False
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(obj.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            inset = QRectF(obj.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
            painter.setBrush(QColor(COLORS["surface"]))
            painter.drawRoundedRect(inset, _POPUP_RADIUS, _POPUP_RADIUS)
            return True
        return False


class AppComboBox(QComboBox):
    """QComboBox that matches the rest of the UI instead of the native boxy panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appCombo")
        self._popup_prepared = False
        self._popup_filter = _PopupChromeFilter(self)
        self._palette = _dark_palette()
        self.setPalette(self._palette)

        fusion = _fusion_style(self)
        if fusion is not None:
            self.setStyle(fusion)

        view = self.view()
        view_fusion = _fusion_style(view)
        if view_fusion is not None:
            view.setStyle(view_fusion)
        view.setFrameShape(QFrame.NoFrame)
        view.setPalette(self._palette)
        view.setMouseTracking(True)
        view.setStyleSheet("background: transparent; border: none; outline: none;")
        viewport = view.viewport()
        viewport.setAutoFillBackground(False)
        viewport.setAttribute(Qt.WA_Hover, True)
        viewport.setPalette(self._palette)
        viewport.setStyleSheet("background: transparent;")

        delegate = _RoundedItemDelegate(self)
        self.setItemDelegate(delegate)
        view.setItemDelegate(delegate)

        chevron_down = _CHEVRON_DOWN.as_posix()
        chevron_up = _CHEVRON_UP.as_posix()
        raised = COLORS["surface_raised"]
        border = COLORS["border"]
        text = COLORS["text"]
        focus = COLORS["border_focus"]
        self.setStyleSheet(f"""
            QComboBox#appCombo {{
                background-color: {raised};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px 28px 4px 10px;
                min-height: 24px;
                combobox-popup: 0;
            }}
            QComboBox#appCombo:hover {{
                background-color: #363636;
            }}
            QComboBox#appCombo:on {{
                border-color: {focus};
            }}
            QComboBox#appCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 22px;
                border: none;
                background: transparent;
            }}
            QComboBox#appCombo::down-arrow {{
                image: url("{chevron_down}");
                width: 14px;
                height: 14px;
            }}
            QComboBox#appCombo::down-arrow:on {{
                image: url("{chevron_up}");
                width: 14px;
                height: 14px;
            }}
        """)

    def showPopup(self):
        super().showPopup()
        self._prepare_popup()

    def _prepare_popup(self) -> None:
        container = self.view().parentWidget()
        if container is None:
            return

        if not self._popup_prepared:
            fusion = _fusion_style(container)
            if fusion is not None:
                container.setStyle(fusion)
            container.setPalette(self._palette)
            container.setFrameShape(QFrame.NoFrame)
            geo = container.geometry()
            container.setWindowFlags(
                Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
            )
            container.setAttribute(Qt.WA_TranslucentBackground, True)
            container.setAttribute(Qt.WA_StyledBackground, True)
            container.setAutoFillBackground(False)
            handle = container.windowHandle()
            parent_handle = self.window().windowHandle() if self.window() else None
            if handle is not None and parent_handle is not None:
                handle.setTransientParent(parent_handle)
            container.installEventFilter(self._popup_filter)
            self._popup_prepared = True
            container.setGeometry(geo)
            container.show()
        container.update()
