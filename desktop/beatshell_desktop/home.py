"""Pantalla de inicio («Sonando ahora») y ecualizador en directo.

Se abre pulsando el logo. Minimalista: la portada grande sobre un fondo hecho con la propia
portada desenfocada, el nombre de la canción y de dónde viene (playlist, álbum…), y un
espectro tenue abajo. Al cambiar de canción, el fondo se funde con el nuevo.

Pensado para no gastar:
- El fondo desenfocado, el halo y la sombra se dibujan una vez por canción (el fondo a media
  resolución, ~1 MB) y se liberan al salir de la pantalla.
- Lo único que se mueve en cada fotograma es la onda del espectro, y solo se repinta esa franja.
- La animación va a 30 fps y solo mientras la pantalla se ve y hay música sonando; en pausa
  o en otra pantalla, el temporizador se para (0 % de CPU).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
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
    QRadialGradient,
)
from PySide6.QtWidgets import QToolButton, QWidget

from BeatShell import eq
from BeatShell.live_eq import response_db
from BeatShell.models import Track, fmt_time

from . import icons
from .widgets import PlayButton, covers, mosaic_sources, paint_cover, scaled, sprite_image

if TYPE_CHECKING:
    from .player import Player
    from .style import Theme

FPS = 30
BARS = 32  # bandas del espectro (en espejo: 64 puntos de la línea)
EQ_H = 310  # alto del panel del ecualizador


# ====================================================================== espectro
_WINDOW = np.hanning(2048).astype(np.float32)
_EDGES: dict[int, np.ndarray] = {}


def spectrum(samples: np.ndarray, samplerate: int, bands: int = BARS) -> np.ndarray:
    """Niveles 0..1 por bandas logarítmicas (sin importar el motor de audio en esta ventana)."""
    if samples is None or len(samples) != len(_WINDOW):
        return np.zeros(bands, dtype=np.float32)
    spec = np.abs(np.fft.rfft(samples * _WINDOW))
    edges = _EDGES.get(samplerate)
    if edges is None:
        f = np.geomspace(35.0, min(16000.0, samplerate / 2 - 1), bands + 1)
        edges = np.clip((f / samplerate * len(_WINDOW)).astype(int), 1, len(spec) - 1)
        edges = np.maximum(edges, np.arange(len(edges)) + 1)
        _EDGES[samplerate] = edges
    out = np.add.reduceat(spec, edges[:-1])[:bands] / np.maximum(np.diff(edges), 1)
    db = 20 * np.log10(np.maximum(out, 1e-6)) + 18
    return np.clip((db + 40) / 52, 0, 1).astype(np.float32)


def cover_colors(img: QImage) -> tuple[QColor, QColor]:
    """Los dos colores principales de la portada (no la media, que suele salir marrón).

    Se reduce a 24×24, se agrupan los píxeles por tono (12 grupos) pesando lo vivos que son, y
    se toman el grupo más fuerte y el siguiente con un tono claramente distinto. Si la portada
    es casi gris, el segundo color es el primero girado 40°.
    """
    small = img.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB32)
    raw = np.frombuffer(small.constBits(), dtype=np.uint8, count=small.sizeInBytes())
    px = raw.reshape(small.height(), small.bytesPerLine() // 4, 4)[:, : small.width(), 2::-1]
    rgb = px.reshape(-1, 3).astype(np.float32) / 255.0  # BGRA → RGB
    mx, mn = rgb.max(1), rgb.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    weight = sat * mx + 1e-4
    r, g, b = rgb.T
    hue = (np.degrees(np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b)) + 360) % 360
    bins = (hue // 30).astype(int)
    score = np.bincount(bins, weights=weight, minlength=12)

    def mean(k: int) -> QColor:
        m = bins == k
        c = (rgb[m] * weight[m, None]).sum(0) / weight[m].sum()
        return QColor.fromRgbF(*np.clip(c, 0, 1))

    order = np.argsort(score)[::-1]
    first = int(order[0])
    primary = mean(first)
    second = next((int(k) for k in order[1:] if score[k] > score[first] * 0.15
                   and min(abs(k - first), 12 - abs(k - first)) >= 2), None)
    if second is None:
        h, s, v, _ = primary.getHsvF()
        secondary = QColor.fromHsvF((max(h, 0) + 40 / 360) % 1.0, s, v)
    else:
        secondary = mean(second)
    return primary, secondary


def _blur(img: QImage, w: int, h: int) -> QImage:
    """Desenfoque barato: reducir mucho y volver a ampliar suavizando (dos pasadas)."""
    side = min(img.width(), img.height())
    sq = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    tiny = sq.scaled(18, 18, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    mid = tiny.scaled(72, 72, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    return mid.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation)


# ====================================================================== ecualizador
class EqPanel(QWidget):
    """10 bandas que se arrastran mientras suena: el cambio se oye al momento.

    Encima, la curva real del ecualizador. Debajo, los presets. Doble clic en una banda la
    deja a 0 dB; la rueda la mueve de medio en medio dB.
    """

    closed = Signal()

    def __init__(self, theme: "Theme", player: "Player") -> None:
        super().__init__()
        self.t = theme
        self.player = player
        self.setMouseTracking(True)
        self.setMinimumHeight(EQ_H - 20)
        self._gains = list(eq.gains(player.cfg.eq_preset, player.cfg.eq_custom))
        self._drag: int | None = None
        self._hover: int | None = None
        self._chips: list[tuple[QRectF, str]] = []
        self._curve: QPainterPath | None = None
        # Se manda como mucho un cambio cada 15 ms (los ratones dan 120+ eventos/s).
        self._send = QTimer(self, singleShot=True, interval=15)
        self._send.timeout.connect(lambda: self.player.set_custom_eq(self._gains))

    # ------------------------------------------------------------ datos
    def sync(self) -> None:
        """Vuelve a leer el preset actual (lo pudo cambiar el menú del EQ)."""
        if self._drag is None:
            self._gains = list(eq.gains(self.player.cfg.eq_preset, self.player.cfg.eq_custom))
            self._curve = None
            self.update()

    def _set_band(self, i: int, db: float) -> None:
        db = round(max(-eq.RANGE_DB, min(eq.RANGE_DB, db)) * 2) / 2  # pasos de 0,5 dB
        if self._gains[i] == db and self.player.cfg.eq_preset == eq.CUSTOM:
            return
        self._gains[i] = db
        self._curve = None
        if not self._send.isActive():
            self._send.start()
        self.update()

    # ------------------------------------------------------------ geometría
    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(28, 58, -28, -100)

    def _col(self, i: int) -> QRectF:
        a = self._area()
        w = a.width() / len(eq.BANDS)
        return QRectF(a.left() + i * w, a.top(), w, a.height())

    def _y(self, db: float) -> float:
        a = self._area().adjusted(0, 14, 0, -22)
        return a.center().y() - db / eq.RANGE_DB * a.height() / 2

    def _db_at(self, y: float) -> float:
        a = self._area().adjusted(0, 14, 0, -22)
        return (a.center().y() - y) / (a.height() / 2) * eq.RANGE_DB

    def _band_at(self, x: float) -> int | None:
        a = self._area()
        if not a.left() <= x <= a.right():
            return None
        return min(len(eq.BANDS) - 1, int((x - a.left()) / (a.width() / len(eq.BANDS))))

    # ------------------------------------------------------------ ratón
    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        p = e.position()
        for rect, name in self._chips:
            if rect.contains(p):
                self._choose(name)
                return
        i = self._band_at(p.x())
        if i is not None and self._area().contains(p):
            self._drag = i
            self._set_band(i, self._db_at(p.y()))

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        p = e.position()
        if self._drag is not None:
            self._set_band(self._drag, self._db_at(p.y()))
            return
        hover = self._band_at(p.x()) if self._area().contains(p) else None
        chip = any(r.contains(p) for r, _ in self._chips)
        self.setCursor(Qt.CursorShape.PointingHandCursor if hover is not None or chip
                       else Qt.CursorShape.ArrowCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        if self._drag is not None:
            self._drag = None
            self._send.stop()
            self.player.set_custom_eq(self._gains, save=True)  # al soltar: se guarda
            self.update()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: ANN001, N802
        i = self._band_at(e.position().x())
        if i is not None and self._area().contains(e.position()):
            self._set_band(i, 0.0)
            self.player.set_custom_eq(self._gains, save=True)

    def wheelEvent(self, e) -> None:  # noqa: ANN001, N802
        i = self._band_at(e.position().x())
        if i is None:
            return
        step = 0.5 if e.angleDelta().y() > 0 else -0.5
        self._set_band(i, self._gains[i] + step)
        self.player.set_custom_eq(self._gains, save=True)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = None
        self.update()

    def _choose(self, name: str) -> None:
        if name == "Restablecer":
            self._gains = [0.0] * len(eq.BANDS)
            self.player.set_custom_eq(self._gains, save=True)
        elif name == eq.CUSTOM:
            self.player.set_custom_eq(self.player.cfg.eq_custom, save=True)
        else:
            self.player.set_eq(name)
        self.sync()

    # ------------------------------------------------------------ dibujo
    def _curve_path(self) -> QPainterPath:
        if self._curve is None:
            a = self._area()
            fs = 48000.0
            freqs = np.geomspace(20.0, 20000.0, 180)
            db = response_db(self._gains, freqs, fs)
            path = QPainterPath()
            for k, (f, d) in enumerate(zip(freqs, db)):
                x = a.left() + (np.log(f / 20.0) / np.log(1000.0)) * a.width()
                y = self._y(float(np.clip(d, -eq.RANGE_DB * 1.4, eq.RANGE_DB * 1.4)))
                (path.lineTo if k else path.moveTo)(QPointF(x, y))
            self._curve = path
        return self._curve

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = QColor(t.bg)
        bg.setAlphaF(0.72)
        p.setPen(QPen(QColor(255, 255, 255, 22) if t.dark else QColor(0, 0, 0, 18), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 18, 18)

        base = QFont(self.font())
        small = scaled(base, 0.82)
        bold = QFont(base)
        bold.setWeight(QFont.Weight.DemiBold)
        p.setFont(bold)
        p.setPen(QColor(t.text))
        preset = self.player.cfg.eq_preset
        p.drawText(QRectF(r.left() + 24, r.top() + 10, 400, 22),
                   Qt.AlignmentFlag.AlignVCenter, f"Ecualizador · {preset}")
        p.setFont(small)
        p.setPen(QColor(t.faint))
        p.drawText(QRectF(r.right() - 344, r.top() + 10, 320, 22),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   "Arrastra mientras suena · doble clic: 0 dB")

        a = self._area()
        # línea de 0 dB y guías de ±6
        for db, alpha in ((0, 60), (6, 22), (-6, 22)):
            pen = QPen(QColor(t.muted), 1, Qt.PenStyle.DashLine if db else Qt.PenStyle.SolidLine)
            c = pen.color()
            c.setAlpha(alpha)
            pen.setColor(c)
            p.setPen(pen)
            y = self._y(db)
            p.drawLine(QPointF(a.left(), y), QPointF(a.right(), y))

        # curva real con relleno degradado
        path = self._curve_path()
        fill = QPainterPath(path)
        fill.lineTo(QPointF(a.right(), self._y(0)))
        fill.lineTo(QPointF(a.left(), self._y(0)))
        fill.closeSubpath()
        g = QLinearGradient(a.topLeft(), a.topRight())
        c1, c2 = QColor(t.accent), QColor(t.accent2)
        c1.setAlpha(46)
        c2.setAlpha(46)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(fill)
        g2 = QLinearGradient(a.topLeft(), a.topRight())
        g2.setColorAt(0, QColor(t.accent))
        g2.setColorAt(1, QColor(t.accent2))
        p.setPen(QPen(g2, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # bandas: carril, valor y tirador
        for i, (label, db) in enumerate(zip(eq.BAND_LABELS, self._gains)):
            col = self._col(i)
            x = col.center().x()
            top, bot = self._y(eq.RANGE_DB), self._y(-eq.RANGE_DB)
            active = i in (self._drag, self._hover)
            rail = QColor(t.muted)
            rail.setAlpha(70 if active else 38)
            p.setPen(QPen(rail, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x, top), QPointF(x, bot))
            y = self._y(db)
            fillc = QColor(t.accent if db >= 0 else t.accent2)
            p.setPen(QPen(fillc, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x, self._y(0)), QPointF(x, y))
            rad = 8.5 if active else 7
            p.setPen(QPen(QColor(t.bg), 2))
            p.setBrush(QColor("#ffffff") if t.dark else QColor(t.text))
            p.drawEllipse(QPointF(x, y), rad, rad)
            p.setFont(small)
            p.setPen(QColor(t.text if active else t.muted))
            p.drawText(QRectF(col.left(), bot + 6, col.width(), 16), Qt.AlignmentFlag.AlignCenter, label)
            if active or db:
                p.setPen(QColor(t.text if active else t.faint))
                p.drawText(QRectF(col.left(), top - 22, col.width(), 16), Qt.AlignmentFlag.AlignCenter,
                           f"{db:+.1f}".replace(".0", "") if db else "0")

        # presets en «píldoras»
        self._chips = []
        fm = QFontMetrics(small)
        names = [eq.CUSTOM, *eq.PRESETS, "Restablecer"]
        x, y = r.left() + 24, r.bottom() - 74
        for name in names:
            w = fm.horizontalAdvance(name) + 22
            if x + w > r.right() - 20:  # segunda fila
                x, y = r.left() + 24, y + 32
                if y > r.bottom() - 30:
                    break
            rect = QRectF(x, y, w, 26)
            on = name == preset
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent) if on else QColor(t.selected))
            p.drawRoundedRect(rect, 13, 13)
            p.setPen(QColor(t.accent_text if on else t.muted))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
            self._chips.append((rect, name))
            x += w + 6
        p.end()


# ====================================================================== inicio
class HomePage(QWidget):
    """«Sonando ahora»: portada grande, de dónde viene y espectro, todo muy limpio."""

    open_context = Signal(tuple)  # ir a la playlist/álbum desde el que suena
    tint_changed = Signal(object)  # (color1, color2) para teñir la app, o None para quitarlo

    def __init__(self, theme: "Theme", player: "Player", settings=None) -> None:  # noqa: ANN001
        super().__init__()
        self.t = theme
        self.player = player
        self.settings = settings
        self._colors: tuple[QColor, QColor] | None = None  # de la portada: espectro y tinte
        self._tinted = False
        self.setMouseTracking(True)
        self._track: Track | None = None
        self._img: QImage | None = None          # portada grande (de la caché compartida)
        self._bg: QPixmap | None = None          # fondo desenfocado (media resolución)
        self._bg_old: QPixmap | None = None      # el anterior, para el fundido entre canciones
        self._strip: tuple | None = None         # trozo del fondo bajo el espectro, ya escalado
        self._fade = 1.0
        self._tint = QColor(theme.accent)
        self._cover_px: QPixmap | None = None
        self._levels = np.zeros(BARS, dtype=np.float32)
        self._pos, self._dur = 0.0, None
        self._eq_open = 0.0                      # 0 cerrado · 1 abierto (animado)
        self._eq_target = 0.0
        self._chip: QRectF | None = None

        self.timer = QTimer(self, interval=1000 // FPS)
        self.timer.timeout.connect(self._tick)

        def tool(name: str, tip: str, size: int = 22) -> QToolButton:
            b = QToolButton(self)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoRaise(True)
            b.setIconSize(QSize(size, size))
            b.setIcon(icons.icon(name, theme.text, theme.accent, size))
            b.setObjectName("homeTool")
            return b

        self.b_prev = tool("prev", "Anterior")
        self.b_next = tool("next", "Siguiente")
        self.b_play = PlayButton(theme, 54)
        self.b_play.setParent(self)
        self.b_eq = tool("eq", "Ecualizador en directo", 20)
        self.b_eq.setCheckable(True)
        self.b_tint = tool("drop", "Toda la app con los colores de la portada (solo en el inicio)", 20)
        self.b_tint.setCheckable(True)
        self.b_tint.setChecked(bool(getattr(settings, "home_tint", False)))
        self.b_tint.toggled.connect(self._toggle_tint)
        self.b_prev.clicked.connect(player.prev)
        self.b_next.clicked.connect(player.next)
        self.b_play.clicked.connect(player.toggle_pause)
        self.b_eq.toggled.connect(self.show_eq)
        self.eq = EqPanel(theme, player)
        self.eq.setParent(self)
        self.eq.hide()
        covers().ready.connect(self._cover_ready)
        player.now_changed.connect(self._on_now)
        player.state_changed.connect(self._on_state)
        player.position.connect(self._on_position)

    # ------------------------------------------------------------ estado
    def retheme(self) -> None:
        for b, name in ((self.b_prev, "prev"), (self.b_next, "next"), (self.b_eq, "eq"),
                        (self.b_tint, "drop")):
            b.setIcon(icons.icon(name, self.t.text, self.t.accent, b.iconSize().width()))
        self._bg = self._bg_old = None
        self._strip = None
        self.update()

    def show_eq(self, on: bool = True) -> None:
        self._eq_target = 1.0 if on else 0.0
        if self.b_eq.isChecked() != on:
            self.b_eq.setChecked(on)
        if on:
            self.eq.sync()
            self.eq.show()
        self._kick()

    def _playing(self) -> bool:
        eng = self.player.engine
        return eng is not None and eng.active and not eng.paused

    def _on_state(self) -> None:
        pl = self.player
        self.b_play.set_icon_name("pause" if self._playing() or pl.loading else "play")
        self.b_play.set_loading(pl.loading is not None)
        self.eq.sync()
        self._kick()
        self.update(self._text_rect().toAlignedRect())

    def _on_position(self, pos: float, dur) -> None:  # noqa: ANN001
        # Llega ~20 veces por segundo (también en pausa): solo se repinta si cambia algo que
        # se vea, el segundo del reloj o un píxel de la barra.
        old = self._progress_key()
        self._pos, self._dur = pos, dur
        if self.isVisible() and self._progress_key() != old:
            self.update(self._progress_rect().adjusted(-60, -8, 60, 8).toAlignedRect())

    def _progress_key(self) -> tuple:
        frac = (self._pos / self._dur) if self._dur else 0.0
        return int(self._pos), int(self._dur or 0), int(frac * self._progress_rect().width())

    def _on_now(self, track: Track | None) -> None:
        if track is not None and self._track is not None and track.key == self._track.key:
            return
        self._track = track
        self._img = None
        self._cover_px = None
        if self._bg is not None:  # fundido del fondo viejo al nuevo
            self._bg_old, self._fade = self._bg, 0.0
        self._bg = None
        self._strip = None
        self._load_cover()
        self._kick()
        self.update()

    def _load_cover(self) -> None:
        t = self._track
        if t is None or not self.isVisible():
            return
        img = covers().image(t, big=True)
        if img is not None:
            self._set_image(img)

    def _cover_ready(self) -> None:
        if self._img is None and self._track is not None and self.isVisible():
            self._load_cover()

    # ------------------------------------------------------------ colores de la portada
    def _toggle_tint(self, on: bool) -> None:
        if self.settings is not None:
            self.settings.home_tint = on
            self.settings.save()
        self._apply_tint()

    def _apply_tint(self) -> None:
        """Tiñe la app si el botón está activo y la pantalla se ve; si no, la deja como estaba."""
        want = self.b_tint.isChecked() and self.isVisible() and self._colors is not None
        if want:
            self._tinted = True
            self.tint_changed.emit(self._colors)
        elif self._tinted:
            self._tinted = False
            self.tint_changed.emit(None)

    def _set_image(self, img: QImage) -> None:
        self._img = img
        self._colors = cover_colors(img)
        h, s, v, _ = self._colors[0].getHsvF()
        self._tint = QColor.fromHsvF(max(h, 0), min(1.0, s * 1.2 + 0.1), min(1.0, max(v, 0.6)))
        self._apply_tint()
        self._cover_px = None
        self._bg = None
        self._strip = None
        self.update()

    # ------------------------------------------------------------ animación
    def _kick(self) -> None:
        """Arranca el temporizador solo si hay algo que animar y la pantalla se ve."""
        if not self.isVisible():
            self.timer.stop()
            return
        busy = (self._playing() or self._levels.any() or self._fade < 1.0
                or abs(self._eq_open - self._eq_target) > 0.001)
        if busy and not self.timer.isActive():
            self.timer.start()

    def _tick(self) -> None:
        playing = self._playing()
        eng = self.player.engine
        if playing and eng is not None:
            target = spectrum(eng.viz_samples(), int(getattr(eng, "samplerate", 48000) or 48000))
        else:
            target = np.zeros(BARS, dtype=np.float32)
        # subida rápida, caída suave
        up = target > self._levels
        self._levels = np.where(up, self._levels + (target - self._levels) * 0.6,
                                self._levels * 0.82).astype(np.float32)
        if not playing and float(self._levels.max()) < 0.01:
            self._levels[:] = 0  # sin esto la caída nunca llega a 0 y el temporizador no para

        dirty = self._bars_rect().adjusted(0, -4, 0, 2)
        if self._fade < 1.0:
            self._fade = min(1.0, self._fade + 1 / (0.6 * FPS))
            if self._fade >= 1.0:
                self._bg_old = None
            dirty = QRectF(self.rect())
        if abs(self._eq_open - self._eq_target) > 0.001:
            self._eq_open += (self._eq_target - self._eq_open) * 0.32
            if abs(self._eq_open - self._eq_target) < 0.01:
                self._eq_open = self._eq_target
                if not self._eq_target:
                    self.eq.hide()
            self._layout()
            dirty = QRectF(self.rect())
        self.update(dirty.toAlignedRect())
        if not playing and self._fade >= 1.0 and self._eq_open == self._eq_target \
                and not self._levels.any():
            self.timer.stop()  # nada que animar: 0 % de CPU

    # ------------------------------------------------------------ geometría
    # Bajo la portada: textos (104) + progreso + controles (≈ 222 px) y, separado de los
    # controles por 32 px, la línea del espectro (30 px) con 24 px de margen abajo.
    _BELOW = 222.0 + 32 + 30 + 24

    def _avail(self) -> float:
        return self.height() - EQ_H * self._eq_open

    def _cover_size(self) -> float:
        h = self._avail()
        return max(96.0, min(380.0, h - self._BELOW - 2 * self._top_margin(), self.width() * 0.42))

    def _top_margin(self) -> float:
        return max(18.0, min(64.0, self._avail() * 0.07))

    def _cover_rect(self) -> QRectF:
        s = self._cover_size()
        block = s + self._BELOW
        top = max(self._top_margin(), (self._avail() - block) / 2)
        return QRectF((self.width() - s) / 2, top, s, s)

    def _text_rect(self) -> QRectF:
        c = self._cover_rect()
        return QRectF(40, c.bottom() + 24, self.width() - 80, 104)

    def _progress_rect(self) -> QRectF:
        t = self._text_rect()
        w = min(460.0, self.width() - 160)
        return QRectF((self.width() - w) / 2, t.bottom() + 6, w, 4)

    def _bars_rect(self) -> QRectF:
        """Franja del espectro: fina, abajo, y siempre a 32 px o más de los controles."""
        w = min(520.0, self.width() - 160.0)
        controls_bottom = self._progress_rect().bottom() + 40 + 30
        bottom = self._avail() - 24
        h = min(34.0, bottom - (controls_bottom + 32))
        if h < 14:  # no cabe sin pegarse a los controles: no se dibuja
            return QRectF()
        return QRectF((self.width() - w) / 2, bottom - h, w, h)

    def _layout(self) -> None:
        pr = self._progress_rect()
        cy = int(pr.bottom() + 40)
        cx = self.width() // 2
        self.b_play.move(cx - self.b_play.width() // 2, cy - self.b_play.height() // 2)
        for b, dx in ((self.b_prev, -78), (self.b_next, 78)):
            b.adjustSize()
            b.move(cx + dx - b.width() // 2, cy - b.height() // 2)
        self.b_eq.adjustSize()
        self.b_eq.move(cx + 150 - self.b_eq.width() // 2, cy - self.b_eq.height() // 2)
        self.b_tint.adjustSize()
        self.b_tint.move(cx - 150 - self.b_tint.width() // 2, cy - self.b_tint.height() // 2)
        panel_w = min(900, self.width() - 48)
        y = int(self.height() - EQ_H * self._eq_open)
        self.eq.setGeometry((self.width() - panel_w) // 2, y + 6, panel_w, EQ_H - 20)

    # ------------------------------------------------------------ Qt
    def resizeEvent(self, e) -> None:  # noqa: ANN001, N802
        self._bg = self._bg_old = None
        self._strip = None
        self._cover_px = None
        self._layout()
        super().resizeEvent(e)

    def showEvent(self, e) -> None:  # noqa: ANN001, N802
        self._on_now(self.player.queue.current)
        self._load_cover()
        self._on_state()
        self._layout()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: ANN001, N802
        # Fuera de la pantalla no se gasta nada: ni temporizador ni imágenes en memoria.
        # Y la app recupera sus colores: el tinte es solo para el inicio.
        self.timer.stop()
        if self._tinted:
            self._tinted = False
            self.tint_changed.emit(None)
        self._bg = self._bg_old = None
        self._strip = None
        self._cover_px = None
        self._img = None
        self._track = None
        super().hideEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        on = self._chip is not None and self._chip.contains(e.position()) \
            and self.player.context_for(self._track) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        ctx = self.player.context_for(self._track)
        if ctx is not None and ctx.sel and self._chip is not None and self._chip.contains(e.position()):
            self.open_context.emit(ctx.sel)

    # ------------------------------------------------------------ dibujo
    def _background(self) -> QPixmap:
        if self._bg is None:
            w, h = max(2, self.width() // 2), max(2, self.height() // 2)
            px = QPixmap(w, h)
            p = QPainter(px)
            if self._img is not None:
                p.drawImage(0, 0, _blur(self._img, w, h))
            else:
                p.fillRect(0, 0, w, h, QColor(self.t.bg))
            # velo del tema: el texto se lee siempre y el fondo no compite con la portada
            veil = QColor(self.t.bg)
            veil.setAlphaF(0.58 if self.t.dark else 0.62)
            p.fillRect(0, 0, w, h, veil)
            g = QLinearGradient(0, h * 0.45, 0, h)
            end = QColor(self.t.bg)
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, end)
            p.fillRect(0, 0, w, h, g)
            vg = QRadialGradient(w / 2, h * 0.35, max(w, h) * 0.75)
            dark = QColor(self.t.bg)
            dark.setAlphaF(0.0)
            vg.setColorAt(0.55, dark)
            dark.setAlphaF(0.55)
            vg.setColorAt(1.0, dark)
            p.fillRect(0, 0, w, h, vg)
            p.end()
            self._bg = px
        return self._bg

    _shadow_cache: tuple[int, QImage] | None = None

    def _shadow(self, size: int) -> QImage:
        if self._shadow_cache is None or self._shadow_cache[0] != size:
            n = 64  # se dibuja pequeña y se amplía suavizando: desenfoque gratis
            img = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(0, 0, 0, 150))
            inset = n * 0.22 / 1.44
            q.drawRoundedRect(QRectF(inset, inset * 0.6, n - 2 * inset, n - 2 * inset), 6, 6)
            q.end()
            tiny = img.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            self._shadow_cache = (size, tiny.scaled(96, 96, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))
        return self._shadow_cache[1]

    def _cover_pixmap(self, size: int) -> QPixmap:
        ratio = max(1.0, self.devicePixelRatioF())
        if self._cover_px is None or self._cover_px.width() != int(size * ratio):
            px = QPixmap(int(size * ratio), int(size * ratio))
            px.setDevicePixelRatio(ratio)
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(0, 0, size, size)
            if self._img is not None:
                path = QPainterPath()
                path.addRoundedRect(rect, 16, 16)
                p.setClipPath(path)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawImage(rect, self._img)
            else:
                t = self._track
                ctx = self.player.context_for(t)
                sources = mosaic_sources(ctx.thumb, ctx.tracks) if ctx else ([t] if t else [])
                if t is None:
                    path = QPainterPath()
                    path.addRoundedRect(rect, 16, 16)
                    p.setClipPath(path)
                    p.drawImage(rect, sprite_image("beatshell"))
                else:
                    paint_cover(p, rect, sources, 16, f"{t.artist}|{t.album or t.title}", big=True)
            p.end()
            self._cover_px = px
        return self._cover_px

    def paintEvent(self, e) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        strip = self._bars_rect().adjusted(0, -4, 0, 2).toAlignedRect()
        if self._fade >= 1.0 and strip.contains(e.rect()):
            # Fotograma del espectro: todo lo que hay debajo (fondo, halo, sombra) es fijo, así
            # que se guarda una «foto» de esa franja y cada fotograma solo la copia y dibuja la onda.
            if self._strip is None or self._strip[0] != strip:
                ratio = self.devicePixelRatioF()
                px = QPixmap(strip.size() * ratio)
                px.setDevicePixelRatio(ratio)
                px.fill(QColor(self.t.bg))
                q = QPainter(px)
                q.setRenderHint(QPainter.RenderHint.Antialiasing)
                q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                q.translate(-strip.x(), -strip.y())
                self._paint_static(q, QRectF(strip))
                q.end()
                self._strip = (strip, px)
            p.drawPixmap(strip.topLeft(), self._strip[1])
            if QRectF(strip).intersects(self._progress_zone()):
                self._paint_progress(p)
            self._paint_bars(p)
        else:
            clip = QRectF(e.rect())
            self._paint_static(p, clip)
            if clip.intersects(self._progress_zone()):
                self._paint_progress(p)
            if clip.intersects(self._bars_rect()):
                self._paint_bars(p)
        p.end()

    def _progress_zone(self) -> QRectF:
        return self._progress_rect().adjusted(-60, -8, 60, 8)

    def _paint_static(self, p: QPainter, clip: QRectF) -> None:
        """Lo que no cambia entre fotogramas, limitado a `clip` (escalar todo costaría ~8 ms)."""
        def background(px: QPixmap) -> None:
            sx, sy = px.width() / max(1, self.width()), px.height() / max(1, self.height())
            p.drawPixmap(clip, px, QRectF(clip.x() * sx, clip.y() * sy,
                                          clip.width() * sx, clip.height() * sy))

        background(self._background())
        if self._bg_old is not None and self._fade < 1.0:
            p.setOpacity(1.0 - self._fade)
            background(self._bg_old)
            p.setOpacity(1.0)
        c = self._cover_rect()
        reach = c.width() * 0.9  # el halo es lo que más se extiende
        if clip.intersects(QRectF(c.center().x() - reach, c.center().y() - reach, 2 * reach, 2 * reach)):
            self._paint_cover(p, c)
        if clip.intersects(self._text_rect()):
            self._paint_texts(p, self._track)

    def _paint_cover(self, p: QPainter, c: QRectF) -> None:

        # halo del color de la portada
        halo = QColor(self._tint)
        center = c.center()
        rad = c.width() * 0.9
        rg = QRadialGradient(center, rad)
        halo.setAlphaF(0.42)
        rg.setColorAt(0.0, halo)
        halo.setAlphaF(0.0)
        rg.setColorAt(1.0, halo)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rg)
        p.drawEllipse(center, rad, rad)

        # sombra suave (desenfocada de verdad, calculada una vez por tamaño)
        sh = self._shadow(int(c.width()))
        pad = c.width() * 0.22
        p.drawImage(c.adjusted(-pad, -pad * 0.6, pad, pad * 1.4), sh)

        p.drawPixmap(c.topLeft(), self._cover_pixmap(int(c.width())))

    def _paint_texts(self, p: QPainter, track: Track | None) -> None:
        t = self.t
        # textos
        tr = self._text_rect()
        title_font = scaled(self.font(), 2.1)
        title_font.setWeight(QFont.Weight.Bold)
        sub_font = scaled(self.font(), 1.12)
        small = scaled(self.font(), 0.86)
        fm_t, fm_s = QFontMetrics(title_font), QFontMetrics(sub_font)
        title = track.title if track else "Nada sonando"
        artist = track.display_artist if track else "Busca algo arriba o abre tu biblioteca"
        p.setFont(title_font)
        p.setPen(QColor(t.text))
        p.drawText(QRectF(tr.left(), tr.top(), tr.width(), fm_t.height()), Qt.AlignmentFlag.AlignHCenter,
                   fm_t.elidedText(title, Qt.TextElideMode.ElideRight, int(tr.width())))
        p.setFont(sub_font)
        p.setPen(QColor(t.muted))
        y = tr.top() + fm_t.height() + 4
        p.drawText(QRectF(tr.left(), y, tr.width(), fm_s.height()), Qt.AlignmentFlag.AlignHCenter,
                   fm_s.elidedText(artist, Qt.TextElideMode.ElideRight, int(tr.width())))

        # «Sonando desde · Playlist · Nombre» (o el álbum de la canción)
        self._chip = None
        ctx = self.player.context_for(track)
        label = None
        if ctx is not None:
            label = f"{ctx.kind} · {ctx.name}" + (f" — {ctx.owner}" if ctx.owner else "")
        elif track is not None and track.album:
            label = f"Álbum · {track.album}"
        if label:
            fm_c = QFontMetrics(small)
            text = fm_c.elidedText(label, Qt.TextElideMode.ElideRight, int(min(tr.width(), 520) - 50))
            w = fm_c.horizontalAdvance(text) + 46
            chip = QRectF(self.width() / 2 - w / 2, y + fm_s.height() + 12, w, 28)
            p.setBrush(QColor(255, 255, 255, 20) if t.dark else QColor(0, 0, 0, 14))
            p.setPen(QPen(QColor(255, 255, 255, 30) if t.dark else QColor(0, 0, 0, 22), 1))
            p.drawRoundedRect(chip, 14, 14)
            mini = QRectF(chip.left() + 6, chip.top() + 5, 18, 18)
            if ctx is not None:
                paint_cover(p, mini, mosaic_sources(ctx.thumb, ctx.tracks), 4, ctx.name)
            else:
                paint_cover(p, mini, [track], 4, track.album)
            p.setFont(small)
            p.setPen(QColor(t.text))
            p.drawText(chip.adjusted(30, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, text)
            self._chip = chip

    def _paint_progress(self, p: QPainter) -> None:
        t = self.t
        small = scaled(self.font(), 0.86)
        # progreso: línea fina con degradado
        pr = self._progress_rect()
        p.setPen(Qt.PenStyle.NoPen)
        rail = QColor(t.muted)
        rail.setAlpha(60)
        p.setBrush(rail)
        p.drawRoundedRect(pr, 2, 2)
        frac = (self._pos / self._dur) if self._dur else 0.0
        if frac > 0:
            g = QLinearGradient(pr.topLeft(), pr.topRight())
            g.setColorAt(0, QColor(t.accent))
            g.setColorAt(1, QColor(t.accent2))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(pr.left(), pr.top(), pr.width() * min(1.0, frac), pr.height()), 2, 2)
        p.setFont(small)
        p.setPen(QColor(t.faint))
        p.drawText(QRectF(pr.left() - 56, pr.top() - 7, 48, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_time(self._pos))
        p.drawText(QRectF(pr.right() + 8, pr.top() - 7, 48, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fmt_time(self._dur))

    def _paint_bars(self, p: QPainter) -> None:
        """Una sola línea fina en espejo (graves en el centro), con los colores de la portada."""
        levels = self._levels
        br = self._bars_rect()
        if br.isEmpty() or not levels.any():
            return
        c1, c2 = self._colors or (QColor(self.t.accent), QColor(self.t.accent2))
        vals = np.concatenate((levels[::-1], levels))
        vals = np.convolve(vals, (0.25, 0.5, 0.25), mode="same")
        xs = np.linspace(br.left(), br.right(), len(vals))
        ys = br.bottom() - vals * br.height()
        line = QPainterPath(QPointF(xs[0], ys[0]))
        for x, y in zip(xs[1:], ys[1:]):
            line.lineTo(QPointF(x, y))
        g = QLinearGradient(br.left(), 0, br.right(), 0)
        for pos, col, a in ((0.0, c2, 0), (0.22, c2, 170), (0.5, c1, 210), (0.78, c2, 170), (1.0, c2, 0)):
            h, s, v, _ = QColor(col).getHsvF()
            c = QColor.fromHsvF(max(h, 0), s, max(v, 0.7) if self.t.dark else min(v, 0.6))
            c.setAlpha(a)
            g.setColorAt(pos, c)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(g, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(line)
