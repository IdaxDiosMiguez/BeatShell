"""Iconos vectoriales dibujados al vuelo (nítidos en cualquier pantalla, sin archivos)."""

from __future__ import annotations

import functools

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_FILL = {
    "play": '<path d="M8 5.6v12.8a1 1 0 0 0 1.52.85l10.2-6.4a1 1 0 0 0 0-1.7L9.52 4.75A1 1 0 0 0 8 5.6z"/>',
    "pause": '<rect x="6" y="5" width="4.2" height="14" rx="1.3"/>'
             '<rect x="13.8" y="5" width="4.2" height="14" rx="1.3"/>',
    "next": '<path d="M4.5 6.3v11.4a.9.9 0 0 0 1.4.75l8.5-5.7a.9.9 0 0 0 0-1.5l-8.5-5.7a.9.9 0 0 0-1.4.75z"/>'
            '<rect x="16.6" y="5" width="2.8" height="14" rx="1.2"/>',
    "prev": '<path d="M19.5 6.3v11.4a.9.9 0 0 1-1.4.75l-8.5-5.7a.9.9 0 0 1 0-1.5l8.5-5.7a.9.9 0 0 1 1.4.75z"/>'
            '<rect x="4.6" y="5" width="2.8" height="14" rx="1.2"/>',
}

_STROKE = {
    "shuffle": '<path d="M16 4h4v4"/><path d="M4 20 20 4"/><path d="M20 16v4h-4"/>'
               '<path d="M14.5 14.5 20 20"/><path d="M4 4l5 5"/>',
    "repeat": '<path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11.5v-1a4.5 4.5 0 0 1 4.5-4.5h12.5"/>'
              '<path d="M7 21.5 3.5 18 7 14.5"/><path d="M20.5 12.5v1A4.5 4.5 0 0 1 16 18H3.5"/>',
    "repeat-one": '<path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11.5v-1a4.5 4.5 0 0 1 4.5-4.5h12.5"/>'
                  '<path d="M7 21.5 3.5 18 7 14.5"/><path d="M20.5 12.5v1A4.5 4.5 0 0 1 16 18H3.5"/>'
                  '<path d="M11 10.5l1.4-1v5"/>',
    "volume": '<path d="M11 5 6.5 9H3.5v6h3l4.5 4z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6"/>'
              '<path d="M18.5 6a8.5 8.5 0 0 1 0 12"/>',
    "volume-low": '<path d="M11 5 6.5 9H3.5v6h3l4.5 4z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6"/>',
    "mute": '<path d="M11 5 6.5 9H3.5v6h3l4.5 4z"/><path d="M16 9.5l5 5"/><path d="M21 9.5l-5 5"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 20.5 20.5"/>',
    "queue": '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h9"/><path d="M17 16v5l3.5-2.5z"/>',
    "library": '<path d="M5 4v16"/><path d="M10 4v16"/><path d="M14.5 4.5l4.5 15"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10.5l5 5 5-5"/><path d="M5 20h14"/>',
    "eq": '<path d="M5 20v-6"/><path d="M5 10V4"/><path d="M12 20v-8"/><path d="M12 8V4"/>'
          '<path d="M19 20v-4"/><path d="M19 12V4"/><path d="M2.5 14h5"/><path d="M9.5 8h5"/>'
          '<path d="M16.5 16h5"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "folder": '<path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "note": '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    "list": '<path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/>'
            '<path d="M4 6h.01"/><path d="M4 12h.01"/><path d="M4 18h.01"/>',
    "album": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.5"/>',
    "artist": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "trash": '<path d="M4 7h16"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7l1 13h9l1-13"/>',
    "dj": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="1.5"/><path d="M12 3.5v3"/>',
}


def _svg(name: str, color: str) -> str:
    if name in _FILL:
        body = f'<g fill="{color}">{_FILL[name]}</g>'
    else:
        body = (f'<g fill="none" stroke="{color}" stroke-width="1.9" stroke-linecap="round" '
                f'stroke-linejoin="round">{_STROKE[name]}</g>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'


@functools.lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    ratio = 2.0
    app = QGuiApplication.instance()
    if app is not None:
        ratio = max(2.0, QGuiApplication.primaryScreen().devicePixelRatio())
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color).encode()))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px.width(), px.height()))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, color: str, active: str | None = None, size: int = 20) -> QIcon:
    """Icono con un color normal y (opcional) otro para hover/activo."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.On)
    return ic
