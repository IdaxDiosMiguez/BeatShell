"""Widgets visuales: logo, portada, visualizador, barra de progreso y tablas."""

from __future__ import annotations

import unicodedata

import numpy as np
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.color import Color
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Tree

from .. import art
from ..audio import spectrum_bands
from ..models import Track, fmt_time

# --------------------------------------------------------------- paleta


def theme_rgb(app, name: str, fallback: str) -> art.RGB:
    try:
        value = getattr(app.current_theme, name, None) or fallback
        return Color.parse(value).rgb
    except Exception:
        return Color.parse(fallback).rgb


def palette(app) -> dict[str, art.RGB]:
    primary = theme_rgb(app, "primary", "#ff4fd8")
    secondary = theme_rgb(app, "secondary", "#5ee7ff")
    accent = theme_rgb(app, "accent", "#ffd166")
    bg = theme_rgb(app, "background", "#0f0b1e")
    fg = theme_rgb(app, "foreground", "#e8e3ff")
    return {
        "primary": primary, "secondary": secondary, "accent": accent,
        "bg": bg, "fg": fg,
        "error": theme_rgb(app, "error", "#ff5c7a"),
        "dim": art.lerp(fg, bg, 0.65),
        "shadow": art.lerp(bg, (0, 0, 0), 0.5) if sum(bg) > 60 else art.lerp(primary, bg, 0.82),
    }


class ThemeAware(Widget):
    """Vuelve a pintarse cuando cambia el tema."""

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda *_: self.on_theme_change(), init=False)

    def on_theme_change(self) -> None:
        self.refresh()


# --------------------------------------------------------------- logo
class Logo(ThemeAware):
    DEFAULT_CSS = "Logo { width: 55; height: 3; }"

    def render(self) -> Text:
        p = palette(self.app)
        return art.logo([p["secondary"], p["primary"], p["accent"]], p["shadow"])


# --------------------------------------------------------------- portada
class Graphics:
    """Cómo se dibujan las portadas. Lo decide __main__ antes de arrancar Textual,
    porque detectar el protocolo de Kitty exige preguntar a la terminal."""

    kitty = False


class CoverArt(ThemeAware):
    """Portada en pixel art.

    En Kitty (y Ghostty) se muestra como imagen real con el protocolo gráfico: píxeles
    grandes y nítidos. En el resto de terminales se dibuja con medios bloques ▀.
    La carga de la imagen ocurre en un hilo aparte.
    """

    DEFAULT_CSS = """
    CoverArt { width: 24; height: 12; }
    CoverArt > #cover-img { display: none; }
    """
    KITTY_GRID = 32  # resolución de la portada pixelada en Kitty

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._track: Track | None = None
        self._pixels: art.Pixels | None = None
        self._px = 0
        self._token = 0
        self.pixel_style = True
        self._img = None

    def compose(self):  # noqa: ANN201
        if Graphics.kitty:
            from textual_image.widget import TGPImage

            self._img = TGPImage(id="cover-img")
            yield self._img

    @property
    def px_size(self) -> int:
        if self._img is not None:
            return self.KITTY_GRID
        return max(4, min(self.size.width, self.size.height * 2))

    def set_track(self, track: Track | None) -> None:
        self._track = track
        self._load()

    def on_resize(self, event: events.Resize) -> None:
        if self._img is not None:
            self._fit_image()
        elif self.px_size != self._px:
            self._load()

    def _fit_image(self) -> None:
        """Ajusta la imagen a un cuadrado según el tamaño real de las celdas."""
        from textual_image._terminal import get_cell_size

        cell = get_cell_size()
        w, h = self.size.width, self.size.height
        if not w or not h:
            return
        cols = max(1, round(h * cell.height / max(1, cell.width)))
        rows = h
        if cols > w:
            cols, rows = w, max(1, round(w * cell.width / max(1, cell.height)))
        self._img.styles.width = cols
        self._img.styles.height = rows
        self._img.styles.margin = ((h - rows) // 2, 0, 0, (w - cols) // 2)

    def _load(self) -> None:
        self._token += 1
        token, track, size = self._token, self._track, self.px_size
        self._px = size
        if track is None:
            self._pixels = None
            if self._img is not None:
                self._img.display = False
            self.refresh()
            return
        # Mientras carga mostramos el sprite generativo (instantáneo).
        self._show(art.sprite_cover(f"{track.artist}|{track.album or track.title}", size))
        pixel = self.pixel_style

        def work() -> None:
            px = art.cover_pixels(track, size, pixel or self._img is not None)
            self.app.call_from_thread(self._loaded, token, px)

        self.run_worker(work, thread=True, group="cover", exclusive=False)

    def _loaded(self, token: int, pixels: art.Pixels) -> None:
        if token == self._token:
            self._show(pixels)

    def _show(self, pixels: art.Pixels) -> None:
        self._pixels = pixels
        if self._img is not None:
            self._img.image = art.pixels_to_image(pixels, scale=16)
            self._img.display = True
            self._fit_image()
        self.refresh()

    def render(self) -> Text:
        if self._pixels is None:
            return self._idle()
        if self._img is not None:
            return Text()
        text = art.render(self._pixels)
        pad = max(0, (self.size.width - self._px) // 2)
        if pad:
            lines = text.split("\n")
            out = Text()
            for i, ln in enumerate(lines):
                out.append(" " * pad)
                out.append_text(ln)
                if i < len(lines) - 1:
                    out.append("\n")
            return out
        return text

    def _idle(self) -> Text:
        p = palette(self.app)
        size = max(4, min(self.size.width, self.size.height * 2))
        # Un pequeño cassette pixelado cuando no suena nada.
        px: art.Pixels = [[None] * size for _ in range(size)]
        body = art.lerp(p["primary"], p["bg"], 0.55)
        edge = p["primary"]
        hole = p["bg"]
        label = art.lerp(p["accent"], p["bg"], 0.2)
        x0, x1 = 1, size - 2
        y0, y1 = size // 4, size - size // 4
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                border = y in (y0, y1) or x in (x0, x1)
                px[y][x] = edge if border else body
        for y in range(y0 + 2, y0 + 4):
            for x in range(x0 + 3, x1 - 2):
                px[y][x] = label
        cy = (y0 + y1) // 2 + 1
        for cx in (size // 3, size - size // 3 - 1):
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if 0 <= cy + dy < size and 0 <= cx + dx < size:
                        px[cy + dy][cx + dx] = hole if (dx or dy) else p["secondary"]
        text = art.render(px)
        pad = max(0, (self.size.width - size) // 2)
        if not pad:
            return text
        out = Text()
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            out.append(" " * pad)
            out.append_text(ln)
            if i < len(lines) - 1:
                out.append("\n")
        return out


# --------------------------------------------------------------- visualizador
_EIGHTHS = " ▁▂▃▄▅▆▇█"


class Visualizer(ThemeAware):
    """Espectro en tiempo real (FFT de lo que suena).

    Barras finas con resolución de 1/8 de celda y un degradado horizontal:
    graves a la izquierda, agudos a la derecha.
    """

    DEFAULT_CSS = "Visualizer { height: 1fr; min-height: 2; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.engine = None
        self._levels = np.zeros(0, dtype=np.float32)
        self._colors: list[str] = []
        self._dim = ""

    def on_theme_change(self) -> None:
        self._colors = []
        self.refresh()

    def _bars(self) -> int:
        return max(1, (self.size.width + 1) // 2)

    def tick(self) -> None:
        n = self._bars()
        if len(self._levels) != n:
            self._levels = np.zeros(n, dtype=np.float32)
            self._colors = []
        eng = self.engine
        playing = eng is not None and eng.active and not eng.paused and not eng.buffering
        if playing:
            target = spectrum_bands(eng.viz_samples(), eng.samplerate, n)
        else:
            target = np.zeros(n, dtype=np.float32)
        # Subida rápida, caída suave: se siente vivo sin parpadear.
        up = target > self._levels
        new = np.where(up, self._levels * 0.3 + target * 0.7, self._levels * 0.84 + target * 0.16)
        if np.any(np.abs(new - self._levels) > 0.004) or np.any(new > 0.004):
            self._levels = new
            self.refresh()

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return Text()
        n = len(self._levels)
        if len(self._colors) != n:
            p = palette(self.app)
            stops = [p["secondary"], p["primary"], p["accent"]]
            self._colors = [art.hexc(art.gradient(stops, i / max(1, n - 1))) for i in range(n)]
            self._dim = art.hexc(art.lerp(p["dim"], p["bg"], 0.4))
        out = Text(no_wrap=True, overflow="crop")
        for row in range(h):
            floor = h - 1 - row  # filas por debajo de esta
            for i in range(min(n, (w + 1) // 2)):
                v = float(self._levels[i]) * h
                fill = v - floor
                if fill >= 1:
                    ch, col = "█", self._colors[i]
                elif fill > 0.06:
                    ch, col = _EIGHTHS[max(1, int(fill * 8))], self._colors[i]
                elif row == h - 1:
                    ch, col = "▁", self._dim  # línea base discreta en silencio
                else:
                    ch, col = " ", self._dim
                out.append(ch, col)
                if i * 2 + 1 < w:
                    out.append(" ")
            if row < h - 1:
                out.append("\n")
        return out


# --------------------------------------------------------------- barra de progreso
class SeekBar(ThemeAware):
    DEFAULT_CSS = "SeekBar { height: 1; }"

    position = reactive(0.0)
    duration = reactive(None)

    class Seek(Message):
        def __init__(self, seconds: float) -> None:
            super().__init__()
            self.seconds = seconds

    def _bar_span(self) -> tuple[int, int]:
        left = len(fmt_time(self.position)) + 1
        right = len(fmt_time(self.duration)) + 1
        return left, max(1, self.size.width - left - right)

    def render(self) -> Text:
        p = palette(self.app)
        left, width = self._bar_span()
        out = Text(no_wrap=True)
        out.append(fmt_time(self.position) + " ", Style(color=art.hexc(p["fg"])))
        frac = 0.0
        if self.duration:
            frac = max(0.0, min(1.0, self.position / self.duration))
        filled = int(frac * width)
        for i in range(width):
            if i < filled:
                c = art.gradient([p["secondary"], p["primary"]], i / max(1, width - 1))
                out.append("━", Style(color=art.hexc(c)))
            elif i == filled and self.duration:
                out.append("●", Style(color=art.hexc(p["accent"]), bold=True))
            else:
                out.append("─", Style(color=art.hexc(p["dim"])))
        out.append(" " + fmt_time(self.duration), Style(color=art.hexc(p["dim"])))
        return out

    def on_click(self, event: events.Click) -> None:
        if not self.duration:
            return
        left, width = self._bar_span()
        x = event.x - left
        if 0 <= x <= width:
            self.post_message(self.Seek(self.duration * x / width))


# --------------------------------------------------------------- tablas
def fold(s: str) -> str:
    """Minúsculas y sin tildes, para filtrar."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def ellipsize(s: str, width: int, style: str = "") -> Text:
    """Recorta respetando el ancho real en celdas (emojis, CJK…)."""
    t = Text(s or "", style=style, no_wrap=True)
    if width > 0:
        t.truncate(width, overflow="ellipsis")
    return t


class TrackTable(DataTable):
    """Tabla de canciones reutilizada en todas las vistas.

    `context` decide qué acciones tienen sentido (quitar/mover solo en cola y playlists).
    """

    BINDINGS = [
        Binding("enter", "select_cursor", "Reproducir", show=True),
        Binding("a", "enqueue", "Añadir a cola"),
        Binding("i", "play_next", "A continuación", show=False),
        Binding("g", "save", "A playlist"),
        Binding("d", "download", "Descargar"),
        Binding("x,delete", "remove", "Quitar", key_display="x"),
        Binding("shift+up", "move(-1)", "Subir", show=False),
        Binding("shift+down", "move(1)", "Bajar", show=False),
        Binding("left", "app.seek(-5)", "-5s", show=False),
        Binding("right", "app.seek(5)", "+5s", show=False),
        Binding("shift+left", "app.seek(-30)", "-30s", show=False),
        Binding("shift+right", "app.seek(30)", "+30s", show=False),
        Binding("A", "enqueue_all", "Añadir todo", show=False),
        Binding("G", "save_all", "Guardar todo", show=False),
        Binding("D", "download_all", "Descargar todo", show=False),
    ]

    class Action(Message):
        """Una acción del usuario sobre una o varias filas."""

        def __init__(self, table: "TrackTable", action: str, index: int, arg: int = 0) -> None:
            super().__init__()
            self.table = table
            self.action = action
            self.index = index
            self.arg = arg

    def __init__(self, context: str, **kw) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kw)
        self.context = context
        self.tracks: list[Track] = []
        self.playing_key: str | None = None
        self.current_index: int | None = None  # solo para la cola
        self._widths: tuple[int, ...] = ()

    # ------------------------------------------------------------ datos
    def _compute_widths(self) -> tuple[int, ...]:
        total = max(30, self.size.width - 2)
        fixed = 2 + 4 + 10 + 8  # marca, nº, duración, padding aproximado
        free = max(20, total - fixed - 6)
        if self.context == "library" or any(t.album for t in self.tracks[:50]):
            t, a = int(free * 0.44), int(free * 0.30)
            return (t, a, free - t - a)
        t = int(free * 0.6)
        return (t, free - t, 0)

    def set_tracks(self, tracks: list[Track], keep_cursor: bool = True) -> None:
        row = self.cursor_row if keep_cursor else 0
        self.tracks = list(tracks)
        self._rebuild()
        if self.tracks:
            self.move_cursor(row=min(max(0, row), len(self.tracks) - 1), animate=False)

    def _rebuild(self) -> None:
        self._widths = self._compute_widths()
        tw, aw, bw = self._widths
        self.clear(columns=True)
        self.add_column(" ", key="mark", width=1)
        self.add_column("#", key="n", width=4)
        self.add_column("Título", key="title", width=tw)
        self.add_column("Artista", key="artist", width=aw)
        if bw:
            self.add_column("Álbum", key="album", width=bw)
        self.add_column("Dur.", key="dur", width=10)
        for i, t in enumerate(self.tracks):
            cells = [
                self._mark(i, t),
                Text(str(i + 1), style="dim", justify="right"),
                ellipsize(t.title, tw),
                ellipsize(t.display_artist if t.artist else "", aw, "italic"),
            ]
            if bw:
                cells.append(ellipsize(t.album, bw, "dim"))
            cells.append(Text(f"{t.icon} {fmt_time(t.duration)}", justify="right", style="dim"))
            self.add_row(*cells, key=str(i))

    def _mark(self, i: int, t: Track) -> Text:
        if self.context == "queue":
            if i == self.current_index:
                return Text("▶", style="bold")
            return Text(" ")
        if self.playing_key and t.key == self.playing_key:
            return Text("♪", style="bold")
        return Text(" ")

    def refresh_marks(self) -> None:
        for i, t in enumerate(self.tracks):
            try:
                self.update_cell(str(i), "mark", self._mark(i, t))
            except Exception:
                break

    def on_resize(self, event: events.Resize) -> None:
        if self.tracks and self._compute_widths() != self._widths:
            row = self.cursor_row
            self._rebuild()
            self.move_cursor(row=min(row, len(self.tracks) - 1), animate=False)

    @property
    def selected(self) -> Track | None:
        r = self.cursor_row
        return self.tracks[r] if self.tracks and 0 <= r < len(self.tracks) else None

    # ------------------------------------------------------------ acciones
    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("remove", "move"):
            return self.context in ("queue", "playlist")
        if action in ("enqueue_all", "save_all", "download_all"):
            return self.context in ("search", "library", "playlist", "queue", "collection")
        return True

    def _emit(self, action: str, arg: int = 0) -> None:
        if not self.tracks:
            return
        self.post_message(self.Action(self, action, self.cursor_row, arg))

    def action_enqueue(self) -> None:
        self._emit("enqueue")

    def action_play_next(self) -> None:
        self._emit("play_next")

    def action_save(self) -> None:
        self._emit("save")

    def action_download(self) -> None:
        self._emit("download")

    def action_remove(self) -> None:
        self._emit("remove")

    def action_move(self, delta: int) -> None:
        self._emit("move", delta)

    def action_enqueue_all(self) -> None:
        self._emit("enqueue_all")

    def action_save_all(self) -> None:
        self._emit("save_all")

    def action_download_all(self) -> None:
        self._emit("download_all")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter, o clic sobre la fila ya resaltada.
        event.stop()
        self._emit("play")


class LibraryTree(Tree):
    """Árbol de la biblioteca. Espacio sigue siendo reproducir/pausa."""

    BINDINGS = [
        Binding("space", "app.toggle_pause", "Play/Pausa", show=False),
        Binding("left,right", "toggle_node", "Abrir/cerrar", show=False),
    ]

    def __init__(self, **kw) -> None:
        super().__init__("Biblioteca", **kw)
        self.show_root = False
        self.guide_depth = 3


class DownloadsTable(DataTable):
    BINDINGS = [
        Binding("c", "app.clear_downloads", "Limpiar terminadas"),
        Binding("y", "app.retry_downloads", "Reintentar fallidas"),
        Binding("o", "app.open_downloads", "Abrir carpeta"),
    ]

