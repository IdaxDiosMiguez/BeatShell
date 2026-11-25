"""Widgets visuales: logo, portada, visualizador, barra de progreso y tablas."""

from __future__ import annotations

import unicodedata

import numpy as np
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.color import Color
from textual.message import Message
from textual.strip import Strip
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

    Barras finas con resolución de 1/8 de celda y un degradado horizontal: graves a la
    izquierda, agudos a la derecha. Se dibuja línea a línea (`render_line`) con estilos
    precalculados y solo se repinta cuando algo cambia de verdad: así gasta muy poca CPU.
    """

    DEFAULT_CSS = "Visualizer { height: 1fr; min-height: 1; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.engine = None
        self._levels = np.zeros(0, dtype=np.float32)
        self._cells = np.zeros(0, dtype=np.int32)  # altura de cada barra en octavos de celda
        self._styles: list[Style] = []
        self._dim = Style()
        self._blank = Style()

    def on_theme_change(self) -> None:
        self._styles = []
        self.refresh()

    def _ensure_styles(self, n: int) -> None:
        if len(self._styles) == n:
            return
        p = palette(self.app)
        stops = [p["secondary"], p["primary"], p["accent"]]
        self._styles = [Style(color=art.hexc(art.gradient(stops, i / max(1, n - 1))))
                        for i in range(n)]
        self._dim = Style(color=art.hexc(art.lerp(p["dim"], p["bg"], 0.4)))

    def tick(self) -> None:
        n = max(1, (self.size.width + 1) // 2)
        if len(self._levels) != n:
            self._levels = np.zeros(n, dtype=np.float32)
            self._cells = np.full(n, -1, dtype=np.int32)
        eng = self.engine
        playing = eng is not None and eng.active and not eng.paused and not eng.buffering
        if playing:
            target = spectrum_bands(eng.viz_samples(), eng.samplerate, n)
        else:
            target = np.zeros(n, dtype=np.float32)
        # Subida rápida, caída suave: se siente vivo sin parpadear.
        up = target > self._levels
        self._levels = np.where(up, self._levels * 0.3 + target * 0.7,
                                self._levels * 0.84 + target * 0.16)
        cells = np.round(self._levels * self.size.height * 8).astype(np.int32)
        if not np.array_equal(cells, self._cells):  # solo si cambia algún octavo de celda
            self._cells = cells
            self.refresh()

    def render_line(self, y: int) -> Strip:
        w, h = self.size.width, self.size.height
        n = len(self._cells)
        if w <= 0 or h <= 0 or n == 0:
            return Strip.blank(w)
        self._ensure_styles(n)
        floor8 = (h - 1 - y) * 8  # octavos que hay por debajo de esta fila
        segs: list[Segment] = []
        bars = min(n, (w + 1) // 2)
        for i in range(bars):
            fill = int(self._cells[i]) - floor8
            if fill >= 8:
                segs.append(Segment("█", self._styles[i]))
            elif fill > 0:
                segs.append(Segment(_EIGHTHS[fill], self._styles[i]))
            elif y == h - 1:
                segs.append(Segment("▁", self._dim))  # línea base discreta en silencio
            else:
                segs.append(Segment(" "))
            if i * 2 + 1 < w:
                segs.append(Segment(" "))
        return Strip(segs).simplify().extend_cell_length(w)


# --------------------------------------------------------------- barra de progreso
class SeekBar(ThemeAware):
    """Barra de progreso clicable. Solo se repinta cuando cambia algo visible."""

    DEFAULT_CSS = "SeekBar { height: 1; }"

    class Seek(Message):
        def __init__(self, seconds: float) -> None:
            super().__init__()
            self.seconds = seconds

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.position = 0.0
        self.duration: float | None = None
        self._sig: tuple | None = None
        self._styles: tuple | None = None

    def on_theme_change(self) -> None:
        self._styles = None
        self._sig = None
        self.refresh()

    def set(self, position: float, duration: float | None) -> None:
        self.position, self.duration = position, duration
        left, width = self._bar_span()
        filled = int(max(0.0, min(1.0, position / duration)) * width) if duration else -1
        sig = (int(position), filled, int(duration or 0), width)
        if sig != self._sig:
            self._sig = sig
            self.refresh()

    def _bar_span(self) -> tuple[int, int]:
        left = len(fmt_time(self.position)) + 1
        right = len(fmt_time(self.duration)) + 1
        return left, max(1, self.size.width - left - right)

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if y != 0 or w <= 0:
            return Strip.blank(w)
        left, width = self._bar_span()
        if self._styles is None or len(self._styles[3]) != width:
            p = palette(self.app)
            grad = [Style(color=art.hexc(art.gradient([p["secondary"], p["primary"]],
                                                      i / max(1, width - 1))))
                    for i in range(width)]
            self._styles = (Style(color=art.hexc(p["fg"])), Style(color=art.hexc(p["dim"])),
                            Style(color=art.hexc(p["accent"]), bold=True), grad)
        fg, dim, head, grad = self._styles
        filled = int(max(0.0, min(1.0, self.position / self.duration)) * width) \
            if self.duration else 0
        segs = [Segment(fmt_time(self.position) + " ", fg)]
        segs += [Segment("━", grad[i]) for i in range(filled)]
        if self.duration and filled < width:
            segs.append(Segment("●", head))
            filled += 1
        if filled < width:
            segs.append(Segment("─" * (width - filled), dim))
        segs.append(Segment(" " + fmt_time(self.duration), dim))
        return Strip(segs).simplify().crop(0, w)

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
        self._keys: list[str] = []
        self._marked: set[int] = set()
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
        self._keys = [t.key for t in self.tracks]  # se calcula una vez, no en cada repintado
        self._rebuild()
        if self.tracks:
            self.move_cursor(row=min(max(0, row), len(self.tracks) - 1), animate=False)

    def _rebuild(self) -> None:
        self._widths = self._compute_widths()
        tw, aw, bw = self._widths
        self.clear(columns=True)
        self._marked = set()
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

    def _is_marked(self, i: int) -> bool:
        if self.context == "queue":
            return i == self.current_index
        return bool(self.playing_key) and i < len(self._keys) and self._keys[i] == self.playing_key

    def _mark(self, i: int, t: Track) -> Text:
        if self._is_marked(i):
            self._marked.add(i)
            return Text("▶" if self.context == "queue" else "♪", style="bold")
        return Text(" ")

    def refresh_marks(self) -> None:
        """Actualiza solo las filas cuya marca cambia (no toda la tabla)."""
        want = {i for i in range(len(self.tracks)) if self._is_marked(i)}
        for i in (self._marked ^ want):
            try:
                self.update_cell(str(i), "mark", self._mark(i, self.tracks[i]) if i in want
                                 else Text(" "))
            except Exception:
                pass
        self._marked = want

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
    """Árbol de la biblioteca.

    Enter (o doble clic) reproduce lo seleccionado: una playlist, un álbum, un artista,
    una carpeta entera… Un clic solo selecciona. Espacio sigue siendo reproducir/pausa y
    ←/→ abren y cierran carpetas.
    """

    BINDINGS = [
        Binding("enter", "play_node", "Reproducir", show=True),
        Binding("space", "app.toggle_pause", "Play/Pausa", show=False),
        Binding("left,right", "toggle_node", "Abrir/cerrar", show=False),
    ]

    class Play(Message):
        """El usuario quiere escuchar el elemento del árbol."""

        def __init__(self, node) -> None:  # noqa: ANN001
            super().__init__()
            self.node = node

    def __init__(self, **kw) -> None:
        super().__init__("Biblioteca", **kw)
        self.show_root = False
        self.guide_depth = 3

    def action_play_node(self) -> None:
        if self.cursor_node is not None:
            self.post_message(self.Play(self.cursor_node))

    def on_click(self, event: events.Click) -> None:
        if event.chain >= 2 and self.cursor_node is not None:
            self.post_message(self.Play(self.cursor_node))


class DownloadsTable(DataTable):
    BINDINGS = [
        Binding("c", "app.clear_downloads", "Limpiar terminadas"),
        Binding("y", "app.retry_downloads", "Reintentar fallidas"),
        Binding("o", "app.open_downloads", "Abrir carpeta"),
    ]

