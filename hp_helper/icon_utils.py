"""Icon loader — recolors source images (monochrome) to a target color for dark-mode UIs.
Supports color=None to load assets with native colors (e.g. logos) without modification.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_ICON_ROOT = Path(__file__).parent / "resources" / "icons"


def load_icon(filename: str, color: str | None = "#ffffff", size: int = 24) -> QIcon:
    """Return a QIcon from *filename* (relative to ``resources/icons/``).
    If *color* is provided, recolors the (monochrome) source to that color.
    If *color* is None, loads the asset with its native colors unchanged.
    Supports PNG, ICO, SVG.
    """
    colored = _recolor(filename, color, size)
    if colored.isNull():
        return QIcon()
    icon = QIcon()
    # Supply common sizes plus the requested size for crisp rendering at different DPIs.
    for s in sorted({16, 24, 32, 48, size}):
        icon.addPixmap(colored.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return icon


def load_pixmap(filename: str, color: str | None = "#ffffff", size: int = 24) -> QPixmap:
    """Return a QPixmap. If color is None, no recoloring is applied."""
    colored = _recolor(filename, color, size)
    if colored.isNull():
        return QPixmap()
    return colored.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _recolor(filename: str, color: str | None, render_size: int) -> QPixmap:
    """Load source (SVG/raster). If color, recolor via SourceIn; else return as-is."""
    path = _resolve(filename)
    if path.suffix == ".svg":
        src = _render_svg(path, max(render_size, 128))
    else:
        src = QPixmap(str(path))
    if src.isNull():
        return QPixmap()
    if color is None:
        return src
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    return out


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
