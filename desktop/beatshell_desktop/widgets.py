"""Piezas de la interfaz: listas de canciones, portada, barra de progreso, avisos."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QEasingCurve,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QGraphicsOpacityEffect,
    QLabel,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from BeatShell import art
from BeatShell.models import Track, fmt_time

from . import icons, workers
from .style import Theme

ROW_H = 56


def scaled(font: QFont, factor: float) -> QFont:
    """Copia de la fuente a otro tamaño (vale tanto si va en puntos como en píxeles)."""
    f = QFont(font)
    if f.pixelSize() > 0:
        f.setPixelSize(max(1, round(f.pixelSize() * factor)))
    elif f.pointSizeF() > 0:
        f.setPointSizeF(f.pointSizeF() * factor)
    return f


# ====================================================================== modelo
class TrackModel(QAbstractListModel):
    def __init__(self, numbered: bool = True) -> None:
        super().__init__()
        self.tracks: list[Track] = []
        self.numbered = numbered
        self.playing_key: str | None = None
        self.playing_uid: str | None = None  # en la cola se marca la entrada exacta

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.tracks)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or index.row() >= len(self.tracks):
            return None
        t = self.tracks[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return t.title
        if role == Qt.ItemDataRole.UserRole:
            return t
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{t.title}\n{t.display_artist}" + (f"\n{t.album}" if t.album else "")
        return None

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def set_playing(self, key: str | None, uid: str | None = None) -> None:
        if (key, uid) == (self.playing_key, self.playing_uid):
            return
        self.playing_key, self.playing_uid = key, uid
        if self.tracks:
            self.dataChanged.emit(self.index(0), self.index(len(self.tracks) - 1))

    def is_playing(self, row: int) -> bool:
        t = self.tracks[row]
        if self.playing_uid is not None:
            return t.uid == self.playing_uid
        return self.playing_key is not None and t.key == self.playing_key


# ====================================================================== portadas en listas
class CoverCache(QObject):
    """Portadas recortadas a cuadrado, cargadas en segundo plano, para pintarlas en listas.

    `image()` devuelve None mientras carga; al llegar se emite `ready` y las listas se repintan.
    """

    ready = Signal()
    SMALL, BIG = 112, 480

    def __init__(self) -> None:
        super().__init__()
        self._img: dict[tuple[str, int], QImage | None] = {}
        self._sprites: dict[str, QImage] = {}
        self._pending: set[tuple[str, int]] = set()
        self._emit = QTimer(self)
        self._emit.setSingleShot(True)
        self._emit.setInterval(60)  # muchas portadas a la vez → un único repintado
        self._emit.timeout.connect(self.ready.emit)

    def image(self, track: Track, big: bool = False) -> QImage | None:
        size = self.BIG if big else self.SMALL
        key = (f"{track.key}|{track.thumb}", size)
        if key in self._img:
            return self._img[key]
        if key not in self._pending:
            self._pending.add(key)
            workers.run(art.cover_bytes, track, done=lambda d, k=key: self._got(k, d))
        return None

    def _got(self, key: tuple[str, int], data: bytes | None) -> None:
        self._pending.discard(key)
        img = QImage.fromData(data) if data else QImage()
        if img.isNull():
            self._img[key] = None
        else:
            side = min(img.width(), img.height())
            img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
            self._img[key] = img.scaled(key[1], key[1], Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
        self._emit.start()

    def sprite(self, seed: str) -> QImage:
        if seed not in self._sprites:
            self._sprites[seed] = sprite_image(seed)
        return self._sprites[seed]


_covers: CoverCache | None = None


def covers() -> CoverCache:
    global _covers
    if _covers is None:
        _covers = CoverCache()
    return _covers


def _thumb_track(url: str) -> Track:
    """Una imagen suelta (portada de playlist) como si fuera una canción, para la caché."""
    return Track(title="", url=url, thumb=url)


def mosaic_sources(thumb: str | None, tracks: list[Track]) -> list[Track]:
    """Como en Spotify: la portada propia, o un mosaico con las 4 primeras portadas distintas."""
    if thumb:
        return [_thumb_track(thumb)]
    seen, out = set(), []
    for t in tracks:
        k = t.thumb or (t.album and f"{t.artist}|{t.album}") or None
        if t.thumb or t.path:
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        if len(out) == 4:
            break
    return out if len(out) >= 4 else out[:1]


def paint_cover(p: QPainter, rect: QRectF, sources: list[Track], radius: float, seed: str,
                big: bool = False, round_: bool = False) -> None:
    """Pinta una portada (o un mosaico 2×2) recortada con esquinas redondeadas."""
    cache = covers()
    path = QPainterPath()
    if round_:
        path.addEllipse(rect)
    else:
        path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)

    def draw(cell: QRectF, img: QImage | None, sprite_seed: str) -> None:
        # las fotos se suavizan; el pixel-art de reserva, no
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, img is not None)
        p.drawImage(cell, img if img is not None else cache.sprite(sprite_seed))

    if len(sources) >= 4:
        w, h = rect.width() / 2, rect.height() / 2
        cells = [QRectF(rect.left() + (i % 2) * w, rect.top() + (i // 2) * h, w, h) for i in range(4)]
        for cell, t in zip(cells, sources):
            draw(cell, cache.image(t, big), f"{t.artist}|{t.album or t.title}")
    else:
        draw(rect, cache.image(sources[0], big) if sources else None, seed)
    p.restore()


ROLE_SUB = Qt.ItemDataRole.UserRole + 1   # «Playlist · autor»
ROLE_ART = Qt.ItemDataRole.UserRole + 2   # (fuentes de portada, semilla, icono de mosaico, redonda)


def paint_tile(p: QPainter, rect: QRectF, th: Theme, icon_name: str, radius: float,
               gradient: bool) -> None:
    """Cuadro con icono para lo que no tiene portada («Todas las canciones», carpetas)."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    if gradient:
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, QColor(th.accent))
        g.setColorAt(1, QColor(th.accent2))
        p.setBrush(g)
    else:
        p.setBrush(QColor(th.selected))
    p.drawRoundedRect(rect, radius, radius)
    isz = int(rect.width() * 0.42)
    px = icons.pixmap(icon_name, "#ffffff" if gradient else th.muted, isz)
    p.drawPixmap(QRectF(rect.center().x() - isz / 2, rect.center().y() - isz / 2, isz, isz),
                 px, QRectF(px.rect()))
    p.restore()


class CollectionDelegate(QStyledItemDelegate):
    """Filas de «Tu biblioteca» como en Spotify: portada, nombre y tipo · autor."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.t = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(180, 58)

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        th = self.t
        r = QRectF(opt.rect).adjusted(0, 1, 0, -1)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if selected or opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(th.selected if selected else th.hover))
            p.drawRoundedRect(r, 7, 7)
        sources, seed, tile, round_ = index.data(ROLE_ART) or ([], "", "list", False)
        cs = 44.0
        cover = QRectF(r.left() + 6, r.top() + (r.height() - cs) / 2, cs, cs)
        if tile:
            paint_tile(p, cover, th, tile, 5, gradient=tile == "note")
        else:
            paint_cover(p, cover, sources, 5, seed, round_=round_)
        x = int(cover.right()) + 11
        w = int(r.right()) - x - 6
        base = QFont(opt.font)
        name = QFont(base)
        name.setWeight(QFont.Weight.Medium)
        small = scaled(base, 0.88)
        fm_n, fm_s = QFontMetrics(name), QFontMetrics(small)
        mid = int(r.top() + r.height() / 2)
        p.setFont(name)
        p.setPen(QColor(th.text))
        p.drawText(QRect(x, mid - fm_n.height() - 1, w, fm_n.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   fm_n.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                   Qt.TextElideMode.ElideRight, w))
        p.setFont(small)
        p.setPen(QColor(th.muted))
        p.drawText(QRect(x, mid + 2, w, fm_s.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   fm_s.elidedText(index.data(ROLE_SUB) or "", Qt.TextElideMode.ElideRight, w))
        p.restore()


class HeroCover(QWidget):
    """Portada grande de la cabecera de una playlist (con mosaico si no tiene propia).

    Si es editable, al pulsarla se emite `clicked` (cambiar la foto) y al pasar por encima se
    insinúa con un velo y un lápiz."""

    clicked = Signal()

    def __init__(self, theme: Theme, size: int = 192) -> None:
        super().__init__()
        self.editable = False
        self._hover = False
        self.setMouseTracking(True)
        self.t = theme
        self.setFixedSize(size, size)
        self._sources: list[Track] = []
        self._seed = ""
        self._tile: str | None = None
        self._round = False
        covers().ready.connect(self.update)

    def set_art(self, sources: list[Track], seed: str, tile: str | None = None,
                round_: bool = False, editable: bool = False) -> None:
        self._sources, self._seed, self._tile, self._round = sources, seed, tile, round_
        self.editable = editable
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self.setToolTip("Cambiar foto" if editable else "")
        self.update()

    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if self.editable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._tile:
            paint_tile(p, rect, self.t, self._tile, 8, gradient=self._tile == "note")
        else:
            paint_cover(p, rect, self._sources, 8, self._seed, big=True, round_=self._round)
        if self.editable and self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRoundedRect(rect, 8, 8)
            px = icons.pixmap("edit", "#ffffff", 36)
            p.drawPixmap(QRectF(rect.center().x() - 18, rect.center().y() - 26, 36, 36), px, QRectF(px.rect()))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(rect.left(), rect.center().y() + 16, rect.width(), 22),
                       Qt.AlignmentFlag.AlignCenter, "Cambiar foto")
        p.end()


class TrackDelegate(QStyledItemDelegate):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.t = theme
        self.anim_phase = 0

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(200, ROW_H)

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        model: TrackModel = index.model()  # type: ignore[assignment]
        t: Track = index.data(Qt.ItemDataRole.UserRole)
        if t is None:
            return
        th = self.t
        r = opt.rect.adjusted(4, 1, -8, -1)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(th.selected if selected else th.hover))
            p.drawRoundedRect(QRectF(r), 8, 8)

        playing = model.is_playing(index.row())
        base = QFont(opt.font)
        small = scaled(base, 0.92)

        # columna izquierda: número o indicador de "sonando"
        lead = QRect(r.left(), r.top(), 40, r.height())
        if playing:
            self._draw_bars(p, lead, QColor(th.accent))
        elif model.numbered:
            p.setFont(small)
            p.setPen(QColor(th.faint))
            p.drawText(lead, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))

        right = r.right() - 12
        dur_w = 52
        p.setFont(small)
        p.setPen(QColor(th.faint))
        p.drawText(QRect(right - dur_w, r.top(), dur_w, r.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_time(t.duration))
        right -= dur_w + 16

        # portada de la canción, como en Spotify
        cs = 40
        cover = QRectF(lead.right() + 6, r.top() + (r.height() - cs) / 2, cs, cs)
        paint_cover(p, cover, [t], 4, f"{t.artist}|{t.album or t.title}")
        x = int(cover.right()) + 12
        avail = right - x
        album_w = int(avail * 0.34) if avail > 520 and t.album else 0
        text_w = avail - album_w - (16 if album_w else 0)

        bold = QFont(base)
        bold.setWeight(QFont.Weight.DemiBold)
        fm_b, fm_s = QFontMetrics(bold), QFontMetrics(small)
        mid = r.top() + r.height() // 2
        p.setFont(bold)
        p.setPen(QColor(th.accent if playing else th.text))
        p.drawText(QRect(x, mid - fm_b.height() - 1, text_w, fm_b.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   fm_b.elidedText(t.title, Qt.TextElideMode.ElideRight, text_w))
        p.setFont(small)
        p.setPen(QColor(th.muted))
        sub = t.display_artist
        p.drawText(QRect(x, mid + 2, text_w, fm_s.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   fm_s.elidedText(sub, Qt.TextElideMode.ElideRight, text_w))
        if album_w:
            p.drawText(QRect(right - album_w, r.top(), album_w, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm_s.elidedText(t.album, Qt.TextElideMode.ElideRight, album_w))
        p.restore()

    def _draw_bars(self, p: QPainter, rect: QRect, color: QColor) -> None:
        heights = ((0.45, 0.9, 0.6), (0.8, 0.5, 0.95), (0.6, 0.75, 0.4), (0.9, 0.55, 0.7))
        h = heights[self.anim_phase % len(heights)]
        w, gap, maxh = 3, 2, 14
        x0 = rect.center().x() - (3 * w + 2 * gap) // 2
        bottom = rect.center().y() + maxh // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for i, frac in enumerate(h):
            bh = max(3, int(maxh * frac))
            p.drawRoundedRect(QRectF(x0 + i * (w + gap), bottom - bh, w, bh), 1.2, 1.2)


class TrackList(QListView):
    play_row = Signal(int)
    menu = Signal(list, QPoint)  # filas seleccionadas, posición global
    delete_rows = Signal(list)

    def __init__(self, theme: Theme, numbered: bool = True, empty: str = "") -> None:
        super().__init__()
        self.model_ = TrackModel(numbered)
        self.setModel(self.model_)
        self.delegate = TrackDelegate(theme, self)
        self.setItemDelegate(self.delegate)
        self.t = theme
        self.empty_text = empty
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.doubleClicked.connect(lambda idx: self.play_row.emit(idx.row()))
        self._anim = QTimer(self)
        self._anim.setInterval(260)
        self._anim.timeout.connect(self._animate)
        self.animate_playing = False
        covers().ready.connect(self.viewport().update)

    # ---------------------------------------------------------------- datos
    @property
    def tracks(self) -> list[Track]:
        return self.model_.tracks

    def set_tracks(self, tracks: list[Track], keep_scroll: bool = False) -> None:
        bar = self.verticalScrollBar().value()
        self.model_.set_tracks(tracks)
        if keep_scroll:
            self.verticalScrollBar().setValue(bar)
        else:
            self.scrollToTop()

    def set_playing(self, key: str | None, uid: str | None = None, animate: bool = False) -> None:
        self.model_.set_playing(key, uid)
        self.animate_playing = animate
        if animate and key and not self._anim.isActive():
            self._anim.start()
        elif not animate:
            self._anim.stop()
            self.viewport().update()

    def _animate(self) -> None:
        self.delegate.anim_phase += 1
        self.viewport().update()

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.selectionModel().selectedRows())

    def selected_tracks(self) -> list[Track]:
        return [self.tracks[r] for r in self.selected_rows() if r < len(self.tracks)]

    # ---------------------------------------------------------------- eventos
    def _context(self, pos: QPoint) -> None:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        if not self.selectionModel().isSelected(idx):
            self.setCurrentIndex(idx)
        self.menu.emit(self.selected_rows(), self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.play_row.emit(self.currentIndex().row())
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_rows():
            self.delete_rows.emit(self.selected_rows())
            return
        super().keyPressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: ANN001, N802
        super().paintEvent(e)
        if not self.tracks and self.empty_text:
            p = QPainter(self.viewport())
            p.setPen(QColor(self.t.faint))
            p.setFont(scaled(p.font(), 1.05))
            p.drawText(self.viewport().rect().adjusted(40, 0, -40, -60),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)


# ====================================================================== portada
def _rounded(src: QPixmap | QImage, size: int, radius: float, ratio: float) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px.width(), px.height()), radius * ratio, radius * ratio)
    p.setClipPath(path)
    img = src if isinstance(src, QImage) else src.toImage()
    w, h = img.width(), img.height()
    side = min(w, h)
    crop = img.copy((w - side) // 2, (h - side) // 2, side, side)
    smooth = side > 64
    mode = Qt.TransformationMode.SmoothTransformation if smooth else Qt.TransformationMode.FastTransformation
    p.drawImage(QRect(0, 0, px.width(), px.height()),
                crop.scaled(px.width(), px.height(), Qt.AspectRatioMode.IgnoreAspectRatio, mode))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def sprite_image(seed: str) -> QImage:
    """La portada pixel-art de BeatShell para canciones sin imagen."""
    px = art.sprite_cover(seed, 16)
    h, w = len(px), len(px[0]) if px else 0
    img = QImage(w, h, QImage.Format.Format_RGB32)
    for y, row in enumerate(px):
        for x, c in enumerate(row):
            r, g, b = c or (0, 0, 0)
            img.setPixel(x, y, (0xFF << 24) | (r << 16) | (g << 8) | b)
    return img


class Cover(QLabel):
    def __init__(self, size: int = 56, radius: float = 8) -> None:
        super().__init__()
        self._size, self._radius = size, radius
        self.setFixedSize(size, size)
        self._key: str | None = None

    def _ratio(self) -> float:
        return max(1.0, self.devicePixelRatioF())

    def set_track(self, track: Track | None) -> None:
        if track is None:
            self._key = None
            self.clear()
            return
        key = f"{track.key}|{track.thumb}"
        if key == self._key:
            return
        self._key = key
        seed = f"{track.artist}|{track.album or track.title}"
        self.setPixmap(_rounded(sprite_image(seed), self._size, self._radius, self._ratio()))

        def loaded(data: bytes | None) -> None:
            if self._key != key or not data:
                return
            img = QImage.fromData(data)
            if not img.isNull():
                self.setPixmap(_rounded(img, self._size, self._radius, self._ratio()))

        workers.run(art.cover_bytes, track, done=loaded)


# ====================================================================== progreso
class Bar(QWidget):
    """Barra de progreso o de volumen dibujada a mano.

    Fina y tranquila en reposo; al pasar el ratón engorda, aparece el tirador y el
    relleno se ilumina. Salta adonde haces clic, se arrastra sin pelear con el
    reproductor y, mientras carga, una luz recorre la pista.
    """

    seek = Signal(float)              # fracción elegida (al soltar; o en vivo si live=True)
    hover_changed = Signal(object)    # fracción bajo el ratón, o None al salir

    def __init__(self, theme: Theme, live: bool = False, gradient: bool = True,
                 wheel_step: float = 0.0) -> None:
        super().__init__()
        self.t = theme
        self.live = live
        self.gradient = gradient
        self.wheel_step = wheel_step
        self.setFixedHeight(20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._frac = 0.0
        self._drag = False
        self._hover: float | None = None
        self._grow = 0.0  # 0 = reposo, 1 = ratón encima
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_grow)
        self._loading = False
        self._phase = 0.0
        self._shimmer = QTimer(self, interval=16)
        self._shimmer.timeout.connect(self._tick_shimmer)

    # ------------------------------------------------------------ API
    @property
    def dragging(self) -> bool:
        return self._drag

    @property
    def fraction(self) -> float:
        return self._frac

    def set_fraction(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        if not self._drag and abs(frac - self._frac) > 1e-4:
            self._frac = frac
            self.update()

    def set_loading(self, on: bool) -> None:
        if on == self._loading:
            return
        self._loading = on
        if on:
            self._phase = 0.0
            self._shimmer.start()
        else:
            self._shimmer.stop()
        self.update()

    # ------------------------------------------------------------ animación
    def _set_grow(self, v) -> None:  # noqa: ANN001
        self._grow = float(v)
        self.update()

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._grow)
        self._anim.setEndValue(target)
        self._anim.start()

    def _tick_shimmer(self) -> None:
        self._phase = (self._phase + 0.012) % 1.4
        self.update()

    # ------------------------------------------------------------ geometría
    def _track(self) -> QRectF:
        pad = 7.0
        h = 4.0 + 2.0 * self._grow
        return QRectF(pad, (self.height() - h) / 2, max(1.0, self.width() - 2 * pad), h)

    def _frac_at(self, x: float) -> float:
        tr = self._track()
        return max(0.0, min(1.0, (x - tr.left()) / tr.width()))

    # ------------------------------------------------------------ ratón
    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = None
        self.hover_changed.emit(None)
        if not self._drag:
            self._animate_to(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = True
        self._frac = self._frac_at(e.position().x())
        if self.live:
            self.seek.emit(self._frac)
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        f = self._frac_at(e.position().x())
        self._hover = f
        self.hover_changed.emit(f)
        if self._drag:
            self._frac = f
            if self.live:
                self.seek.emit(f)
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        if not self._drag:
            return
        self._drag = False
        self.seek.emit(self._frac)
        if not self.underMouse():
            self._animate_to(0.0)
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: ANN001, N802
        if not self.wheel_step:
            return super().wheelEvent(e)
        steps = e.angleDelta().y() / 120 or e.angleDelta().x() / 120
        if steps:
            self._frac = max(0.0, min(1.0, self._frac + steps * self.wheel_step))
            self.seek.emit(self._frac)
            self.update()
        e.accept()

    # ------------------------------------------------------------ dibujo
    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tr = self._track()
        r = tr.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.selected))
        p.drawRoundedRect(tr, r, r)

        if self._loading:
            # luz que recorre la pista
            w = tr.width() * 0.28
            x = tr.left() + (self._phase - 0.2) * tr.width()
            g = QLinearGradient(x, 0, x + w, 0)
            c = QColor(t.accent)
            c0 = QColor(c)
            c0.setAlpha(0)
            g.setColorAt(0.0, c0)
            g.setColorAt(0.5, c)
            g.setColorAt(1.0, c0)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(tr, r, r)
            p.setClipPath(clip)
            p.setBrush(g)
            p.drawRect(QRectF(x, tr.top(), w, tr.height()))
            p.restore()
            return

        fill_w = tr.width() * self._frac
        if fill_w > 0.5:
            fill = QRectF(tr.left(), tr.top(), max(fill_w, tr.height()), tr.height())
            if self.gradient:
                g = QLinearGradient(tr.left(), 0, tr.right(), 0)
                g.setColorAt(0.0, QColor(t.accent))
                g.setColorAt(1.0, QColor(t.accent2))
                brush = g
            else:
                c = QColor(t.text)
                a = QColor(t.accent)
                k = self._grow
                brush = QColor(int(c.red() + (a.red() - c.red()) * k),
                               int(c.green() + (a.green() - c.green()) * k),
                               int(c.blue() + (a.blue() - c.blue()) * k))
            p.setBrush(brush)
            p.drawRoundedRect(fill, r, r)

        # marca fantasma bajo el ratón (dónde saltarías)
        if self._hover is not None and not self._drag and self.gradient:
            hx = tr.left() + tr.width() * self._hover
            ghost = QColor(t.text)
            ghost.setAlpha(60)
            p.setBrush(ghost)
            p.drawRoundedRect(QRectF(hx - 1, tr.top() - 2, 2, tr.height() + 4), 1, 1)

        # tirador
        k = 1.0 if self._drag else self._grow
        if k > 0.01:
            kx = tr.left() + fill_w
            cy = tr.center().y()
            rad = 4.0 + 2.5 * k
            shadow = QColor(0, 0, 0, int(70 * k))
            p.setBrush(shadow)
            p.drawEllipse(QPointF(kx, cy + 1), rad + 1.5, rad + 1.5)
            knob = QColor("#ffffff")
            knob.setAlphaF(k)
            p.setBrush(knob)
            p.drawEllipse(QPointF(kx, cy), rad, rad)


class PlayButton(QAbstractButton):
    """Botón redondo de reproducir/pausa con un leve crecimiento al pasar el ratón y un
    anillo que gira mientras carga."""

    def __init__(self, theme: Theme, size: int = 40) -> None:
        super().__init__()
        self.t = theme
        self.setFixedSize(size + 8, size + 8)
        self._d = size
        self._icon = "play"
        self._scale = 1.0
        self._loading = False
        self._angle = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self, duration=120, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_scale)
        self._spin = QTimer(self, interval=16)
        self._spin.timeout.connect(self._tick)
        self.pressed.connect(lambda: self._scale_to(0.92))
        self.released.connect(lambda: self._scale_to(1.06 if self.underMouse() else 1.0))

    def set_icon_name(self, name: str) -> None:
        if name != self._icon:
            self._icon = name
            self.update()

    def set_loading(self, on: bool) -> None:
        if on == self._loading:
            return
        self._loading = on
        self._spin.start() if on else self._spin.stop()
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 7) % 360
        self.update()

    def _set_scale(self, v) -> None:  # noqa: ANN001
        self._scale = float(v)
        self.update()

    def _scale_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self._scale_to(1.06)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._scale_to(1.0)
        super().leaveEvent(e)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._d + 8, self._d + 8)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        rad = self._d / 2 * self._scale
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.text))
        p.drawEllipse(c, rad, rad)
        isz = int(self._d * 0.5)
        px = icons.pixmap(self._icon, t.bg, isz)
        p.drawPixmap(QPointF(c.x() - isz / 2, c.y() - isz / 2), px)
        if self._loading:
            pen = QPen(QColor(t.accent), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ring = rad + 3.5
            p.drawArc(QRectF(c.x() - ring, c.y() - ring, 2 * ring, 2 * ring),
                      -self._angle * 16, 100 * 16)


# ====================================================================== aviso
class Toast(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMaximumWidth(520)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._faded)
        self._fading_out = False
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self._fade_out)
        self.bottom_margin = 110
        self.hide()

    def show_message(self, text: str, error: bool = False) -> None:
        self.setText(text)
        self.setProperty("error", "true" if error else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self._place()
        self._anim.stop()
        self._fading_out = False
        self._fx.setOpacity(0.0)
        self.show()
        self.raise_()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide.start(4500 if error else 2600)

    def _place(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - self.bottom_margin)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._fading_out = True
        self._anim.start()

    def _faded(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()


# ====================================================================== descargas
class DownloadModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: list = []
        self._rows: dict[str, int] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.jobs)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.UserRole:
            return self.jobs[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.jobs[index.row()].track.title
        return None

    def upsert(self, job) -> None:  # noqa: ANN001
        row = self._rows.get(job.id)
        if row is None:
            self.beginInsertRows(QModelIndex(), 0, 0)
            self.jobs.insert(0, job)
            self._rows = {j.id: i for i, j in enumerate(self.jobs)}
            self.endInsertRows()
        else:
            self.dataChanged.emit(self.index(row), self.index(row))

    def reset(self, jobs: list) -> None:
        self.beginResetModel()
        self.jobs = list(reversed(jobs))
        self._rows = {j.id: i for i, j in enumerate(self.jobs)}
        self.endResetModel()


class DownloadDelegate(QStyledItemDelegate):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.t = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(200, 58)

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        job = index.data(Qt.ItemDataRole.UserRole)
        if job is None:
            return
        th = self.t
        r = opt.rect.adjusted(4, 2, -8, -2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(th.hover))
            p.drawRoundedRect(QRectF(r), 8, 8)
        t = job.track
        failed = job.status == "error"
        done = job.finished and not failed
        base = QFont(opt.font)
        bold = QFont(base)
        bold.setWeight(QFont.Weight.DemiBold)
        small = scaled(base, 0.92)
        x, w = r.left() + 14, r.width() - 28
        state_w = 220
        name = f"{t.artist} - {t.title}" if t.artist else t.title
        fm = QFontMetrics(bold)
        p.setFont(bold)
        p.setPen(QColor(th.text))
        p.drawText(QRect(x, r.top() + 8, w - state_w, fm.height()), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(name, Qt.TextElideMode.ElideRight, w - state_w))
        p.setFont(small)
        p.setPen(QColor(th.error if failed else th.muted))
        state = job.error if failed else job.stage
        fs = QFontMetrics(small)
        p.drawText(QRect(x + w - state_w, r.top() + 8, state_w, fs.height()),
                   Qt.AlignmentFlag.AlignRight, fs.elidedText(state, Qt.TextElideMode.ElideRight, state_w))
        p.setPen(QColor(th.faint))
        p.drawText(QRect(x, r.top() + 10 + fm.height(), w, fs.height()), Qt.AlignmentFlag.AlignLeft,
                   job.group)
        # barra
        bar = QRectF(x, r.bottom() - 9, w, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(th.selected))
        p.drawRoundedRect(bar, 1.5, 1.5)
        frac = 1.0 if job.finished else max(0.0, min(1.0, job.progress))
        p.setBrush(QColor(th.error if failed else (th.muted if done else th.accent)))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()), 1.5, 1.5)
        p.restore()


# ====================================================================== ajustes
class Switch(QAbstractButton):
    """Interruptor sí/no con la bolita deslizándose."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.t = theme
        self.setCheckable(True)
        self.setFixedSize(40, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        self.toggled.connect(self._animate)

    def _moved(self, v) -> None:  # noqa: ANN001
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 24)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 2, self.width() - 2, self.height() - 4)
        off, on = QColor(t.selected), QColor(t.accent)
        k = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * k),
                     int(off.green() + (on.green() - off.green()) * k),
                     int(off.blue() + (on.blue() - off.blue()) * k))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        r = track.height() / 2 - 3
        x = track.left() + 3 + r + (track.width() - 6 - 2 * r) * k
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x, track.center().y()), r, r)


class ThemeCard(QAbstractButton):
    """Miniatura de un tema: así se ve antes de elegirlo."""

    def __init__(self, key: str, name: str, preview: Theme | None, current: Theme,
                 light: Theme | None = None) -> None:
        super().__init__()
        self.key, self.name = key, name
        self.preview, self.light = preview, light  # light: segunda mitad del «Automático»
        self.t = current
        self.setCheckable(True)
        self.setFixedSize(128, 96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)

    def _mini(self, p: QPainter, rect: QRectF, th: Theme) -> None:
        p.setBrush(QColor(th.bg))
        p.drawRect(rect)
        side = QRectF(rect.left(), rect.top(), rect.width() * 0.26, rect.height())
        p.setBrush(QColor(th.side))
        p.drawRect(side)
        x = side.right() + 8
        for i, (w, c) in enumerate(((0.42, th.text), (0.3, th.muted), (0.36, th.text), (0.24, th.muted))):
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(x, rect.top() + 9 + i * 8, (rect.right() - x - 6) * (w / 0.42), 3.5), 1.5, 1.5)
        bar = QRectF(x, rect.bottom() - 12, rect.right() - x - 8, 4)
        p.setBrush(QColor(th.selected))
        p.drawRoundedRect(bar, 2, 2)
        g = QLinearGradient(bar.left(), 0, bar.right(), 0)
        g.setColorAt(0, QColor(th.accent))
        g.setColorAt(1, QColor(th.accent2))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * 0.62, bar.height()), 2, 2)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(2, 2, self.width() - 4, 62)
        clip = QPainterPath()
        clip.addRoundedRect(card, 9, 9)
        p.save()
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        if self.preview is not None:
            self._mini(p, card, self.preview)
        else:  # automático: mitad oscuro, mitad claro
            from .style import DARK, LIGHT

            self._mini(p, card, DARK)
            half = QPainterPath()
            half.moveTo(card.right(), card.top())
            half.lineTo(card.right(), card.bottom())
            half.lineTo(card.left() + card.width() * 0.35, card.bottom())
            half.lineTo(card.left() + card.width() * 0.65, card.top())
            p.setClipPath(half, Qt.ClipOperation.IntersectClip)
            self._mini(p, card, LIGHT)
        p.restore()
        sel = self.isChecked()
        hover = self.underMouse()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(t.accent if sel else (t.faint if hover else t.border)), 2 if sel else 1))
        p.drawRoundedRect(card.adjusted(-1, -1, 1, 1), 10, 10)
        f = scaled(p.font(), 0.95)
        f.setWeight(QFont.Weight.DemiBold if sel else QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(t.text if sel else t.muted))
        p.drawText(QRectF(0, card.bottom() + 6, self.width(), 22), Qt.AlignmentFlag.AlignHCenter, self.name)

    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self.update()
        super().leaveEvent(e)
