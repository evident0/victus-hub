"""Icon loader optimized for white monochrome UI assets.

Assets are assumed monochrome white (or native multi-color logos). We never
recolor, decode at the target size when possible, and cache by (path, size).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtSvg import QSvgRenderer


_ICON_ROOT = Path(__file__).resolve().parent.parent / "resources" / "icons"

# (filename, size) -> scaled pixmap / icon
_pixmap_cache: dict[tuple[str, int], QPixmap] = {}
_icon_cache: dict[tuple[str, int], QIcon] = {}


def load_icon(filename: str, color: str | None = None, size: int = 24) -> QIcon:
    """Return a QIcon from *filename* (relative to ``resources/icons/``).

    *color* is accepted for call-site compatibility but ignored — icons are
    white monochrome (or native logos) and are not recolored.

    Only the requested size (plus a 2× HiDPI variant) is decoded and cached.
    """
    del color  # unused — white monochrome assets need no recolor
    size = max(1, int(size))
    key = (filename, size)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    pm = _load_scaled(filename, size)
    if pm.isNull():
        return QIcon()

    icon = QIcon()
    icon.addPixmap(pm)
    # One higher-res variant for fractional/HiDPI displays
    hi = size * 2
    if hi != size:
        pm_hi = _load_scaled(filename, hi)
        if not pm_hi.isNull():
            icon.addPixmap(pm_hi)

    _icon_cache[key] = icon
    return icon


def load_pixmap(filename: str, color: str | None = None, size: int = 24) -> QPixmap:
    """Return a pixmap scaled to *size*. *color* is ignored (see ``load_icon``)."""
    del color
    size = max(1, int(size))
    return _load_scaled(filename, size)


def _load_scaled(filename: str, size: int) -> QPixmap:
    """Decode at *size* and cache. Avoids retaining full source resolution."""
    key = (filename, size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    path = _resolve(filename)
    if not path.exists() and not Path(filename).exists():
        return QPixmap()

    if path.suffix.lower() == ".svg":
        pm = _render_svg(path, size)
    else:
        pm = _read_raster_scaled(path, size)

    if not pm.isNull():
        _pixmap_cache[key] = pm
    return pm


def _read_raster_scaled(path: Path, size: int) -> QPixmap:
    """Decode a raster image directly to *size* when the reader supports it."""
    reader = QImageReader(str(path))
    if not reader.canRead():
        # Fallback: full load + scale (still discard full-res after)
        src = QPixmap(str(path))
        if src.isNull():
            return QPixmap()
        if src.width() <= size and src.height() <= size:
            return src
        return src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # Prefer decoding at target size — PNG readers honor setScaledSize.
    orig = reader.size()
    if orig.isValid() and (orig.width() > size or orig.height() > size):
        # Keep aspect ratio inside size×size box
        target = QSize(size, size)
        scaled = orig.scaled(target, Qt.KeepAspectRatio)
        reader.setScaledSize(scaled)

    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


def _resolve(filename: str) -> Path:
    p = _ICON_ROOT / filename
    if p.exists():
        return p
    return Path(filename)


def _render_svg(path: Path, size: int) -> QPixmap:
    from PySide6.QtGui import QPainter

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QPixmap()
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def clear_icon_caches() -> None:
    """Drop cached icons/pixmaps (mainly for tests)."""
    _pixmap_cache.clear()
    _icon_cache.clear()
