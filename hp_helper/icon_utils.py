"""Icon loader — recolors source images to a target color for dark-mode UIs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer


_ICON_ROOT = Path(__file__).parent / "resources" / "icons"


def load_icon(filename: str, color: str = "#ffffff", size: int = 24) -> QIcon:
    """Return a QIcon from *filename* (relative to ``resources/icons/``),
    recoloring the source to *color* for visibility on dark backgrounds.

    Supports PNG and ICO; SVG rendered via QSvgRenderer when the engine is present.
    """
    path = _resolve(filename)

    if path.suffix == ".svg":
        # Render SVG into a pixmap at the target size then recolor.
        src = _render_svg(path, max(size, 128))
    else:
        src = QPixmap(str(path))

    if src.isNull():
        return QIcon()

    colored = QPixmap(src.size())
    colored.fill(Qt.transparent)
    p = QPainter(colored)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(colored.rect(), QColor(color))
    p.end()

    icon = QIcon()
    # Supply common sizes for crisp rendering at different DPIs.
    for s in (16, 24, 32, 48):
        if s <= size or s == max(16, size):
            icon.addPixmap(colored.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return icon


def load_pixmap(filename: str, color: str = "#ffffff", size: int = 24) -> QPixmap:
    """Return a QPixmap recolored like :func:`load_icon`."""
    path = _resolve(filename)

    if path.suffix == ".svg":
        src = _render_svg(path, max(size, 128))
    else:
        src = QPixmap(str(path))

    if src.isNull():
        return QPixmap()

    colored = QPixmap(src.size())
    colored.fill(Qt.transparent)
    p = QPainter(colored)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(colored.rect(), QColor(color))
    p.end()
    return colored.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _resolve(filename: str) -> Path:
    p = _ICON_ROOT / filename
    if p.exists():
        return p
    # Fallback — try exact path
    return Path(filename)


def _render_svg(path: Path, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    renderer = QSvgRenderer(str(path))
    renderer.render(QPainter(pm))
    return pm
