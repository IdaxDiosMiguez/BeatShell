"""Piezas de la interfaz: listas de canciones, portada, barra de progreso, avisos."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QEasingCurve,
    QModelIndex,
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

ROW_H = 50


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

        x = lead.right() + 6
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
        try:
            self._anim.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
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
        self._anim.finished.connect(self.hide)
        self._anim.start()


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
