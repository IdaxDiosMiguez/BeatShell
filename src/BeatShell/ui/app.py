"""Aplicación principal de BeatShell (Textual)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    Label,
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from .. import art, eq
from ..audio import AudioEngine, AudioError
from ..config import Config
from ..library import (
    AUDIO_EXTS,
    Library,
    MatchCache,
    PlaylistStore,
    load_session,
    save_session,
    track_from_file,
)
from ..models import Track, fmt_time
from ..playqueue import PlayQueue
from ..services import FAILED, DownloadJob, Downloader, Resolver
from ..sources import Collection, classify, spotify, youtube
from .library_view import ALL, LibraryMixin
from .screens import EqualizerScreen, HelpScreen, TextPrompt
from .widgets import (
    CoverArt,
    DownloadsTable,
    LibraryTree,
    Logo,
    SeekBar,
    TrackTable,
    Visualizer,
    palette,
)

THEMES = [
    Theme(
        name="beatshell", primary="#ff4fd8", secondary="#5ee7ff", accent="#ffd166",
        foreground="#ece6ff", background="#0f0b1e", surface="#161130", panel="#221a44",
        success="#7cf29a", warning="#ffd166", error="#ff5c7a", dark=True,
    ),
    Theme(
        name="beatshell-gameboy", primary="#9bbc0f", secondary="#8bac0f", accent="#e0f8d0",
        foreground="#c4dc8c", background="#0f1f0f", surface="#152a15", panel="#306230",
        success="#9bbc0f", warning="#e0f8d0", error="#f87858", dark=True,
    ),
    Theme(
        name="beatshell-sunset", primary="#ff8a3d", secondary="#ff4f6d", accent="#ffe066",
        foreground="#fff1e0", background="#1a0f12", surface="#24141a", panel="#3a1e24",
        success="#a7f070", warning="#ffe066", error="#ff4f6d", dark=True,
    ),
    Theme(
        name="beatshell-ice", primary="#5ab8ff", secondary="#a78bfa", accent="#7ff0d8",
        foreground="#e6f1ff", background="#0a1020", surface="#10182c", panel="#1a2644",
        success="#7ff0d8", warning="#ffd27f", error="#ff7a90", dark=True,
    ),
]

VIEWS = [
    ("queue", "♫  Cola"),
    ("search", "⌕  Buscar"),
    ("library", "▤  Biblioteca"),
    ("downloads", "⇩  Descargas"),
]

# Acciones que solo tienen sentido en la vista Biblioteca
LIBRARY_ACTIONS = {"add_folder", "rescan", "new_playlist", "new_folder", "move_item", "rename",
                   "export_playlist", "import_playlist", "refresh_artist"}
# Letras que, con una caja de texto enfocada, deben escribirse en vez de actuar
TYPING_BLOCKED = {"toggle_pause", "next", "prev", "volume", "mute", "shuffle", "repeat",
                  "cycle_theme", "focus_search", "equalizer", "toggle_dj"} | LIBRARY_ACTIONS


def _bar(frac: float, color: str, width: int = 12) -> Text:
    filled = int(round(frac * width))
    t = Text(no_wrap=True)
    t.append("▰" * filled, style=color)
    t.append("▱" * (width - filled), style="dim")
    t.append(f" {int(frac * 100):>3}%")
    return t


class BeatShellApp(LibraryMixin, App):
    TITLE = "BeatShell"
    CSS_PATH = "beatshell.tcss"
    ENABLE_COMMAND_PALETTE = True

    BINDINGS = [
        Binding("space", "toggle_pause", "Play/Pausa", key_display="␣"),
        Binding("n", "next", "Siguiente"),
        Binding("b", "prev", "Anterior", show=False),
        Binding("plus,equals_sign", "volume(5)", "Volumen", key_display="+/−"),
        Binding("minus", "volume(-5)", "Vol−", key_display="−", show=False),
        Binding("m", "mute", "Silencio", show=False),
        Binding("s", "shuffle", "Aleatorio", show=False),
        Binding("r", "repeat", "Repetir", show=False),
        Binding("E", "equalizer", "Ecualizador", show=False),
        Binding("j", "toggle_dj", "Modo DJ", show=False),
        Binding("slash", "focus_search", "Buscar", key_display="/"),
        Binding("question_mark", "help", "Ayuda", key_display="?"),
        Binding("t", "cycle_theme", "Tema", show=False),
        Binding("q", "quit", "Salir"),
        Binding("1", "view('queue')", "Cola", show=False),
        Binding("2", "view('search')", "Buscar", show=False),
        Binding("3", "view('library')", "Biblioteca", show=False),
        Binding("4", "view('downloads')", "Descargas", show=False),
        Binding("f", "add_folder", "Añadir carpeta", show=False),
        Binding("R", "rescan", "Re-escanear", show=False),
        Binding("N", "new_playlist", "Nueva playlist", show=False),
        Binding("C", "new_folder", "Nueva carpeta", show=False),
        Binding("M", "move_item", "Mover", show=False),
        Binding("e", "rename", "Renombrar", show=False),
        Binding("U", "refresh_artist", "Actualizar artista", show=False),
        Binding("ctrl+e", "export_playlist", "Exportar", show=False),
        Binding("ctrl+o", "import_playlist", "Importar", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Menú", key_display="^B", show=False),
        Binding("escape", "escape", show=False),
    ]

    def __init__(self, initial: str | None = None) -> None:
        super().__init__()
        self._initial = initial
        self.cfg = Config.load()
        if self.cfg.eq_preset not in eq.PRESETS:
            self.cfg.eq_preset = eq.DEFAULT
        self.library = Library()
        self.playlists = PlaylistStore()
        self.matches = MatchCache()
        self.resolver = Resolver(self.matches, self.library)
        self.queue = PlayQueue()
        self.queue.repeat = self.cfg.repeat
        self.queue.shuffle = self.cfg.shuffle
        self.engine: AudioEngine | None = None
        self.engine_error: str | None = None
        try:
            self.engine = AudioEngine(volume=self.cfg.volume)
            self.engine.set_filters(eq.filter_chain(self.cfg.eq_preset, self.cfg.dj_mode))
        except (AudioError, Exception) as exc:  # noqa: BLE001
            self.engine_error = str(exc)
        self.downloader = Downloader(self.cfg, self.resolver, self.library, self._from_downloader)
        self._main_thread = threading.get_ident()
        self._load_token = 0
        self._loading: Track | None = None
        self._fail_streak = 0
        self._muted_volume: int | None = None
        self._queue_version_seen = -1
        self._session_saved_version = -1
        self._session_saved_at = 0.0
        self._results: Collection | None = None
        self._last_status = ""
        self._groups: dict[str, list[str]] = {}
        self._prefetch_timer = None
        self._sidebar_hint_shown = False
        self._xfade_for = -1  # token de la pista que ya inició su fundido
        self._radio_for: str | None = None
        self._hq: tuple[int, object] | None = None  # (token, Source HQ) de la pista actual
        self._hq_tries = 0
        self._lib_sel = ALL
        self._lib_tracks: list[Track] = []
        self._lib_expanded: set[str] = {"__local__"}

    # ================================================================ layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Logo()
            yield Input(
                placeholder="Busca en YouTube · pega un enlace de YouTube/Spotify · o una ruta   ( / )",
                id="omnibar",
            )
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield OptionList(*[Option(label, id=key) for key, label in VIEWS], id="nav")
                yield Static(id="sidebar-stats")
            with ContentSwitcher(initial="queue", id="views"):
                with Vertical(id="queue"):
                    yield Label("Cola de reproducción", id="queue-header", classes="view-header")
                    yield Label("", id="queue-hint", classes="view-hint")
                    yield TrackTable("queue", id="queue-table")
                with Vertical(id="search"):
                    yield Label("Buscar", id="search-header", classes="view-header")
                    yield Label("", id="search-hint", classes="view-hint")
                    yield LoadingIndicator(id="search-loading")
                    yield TrackTable("search", id="search-table")
                with Vertical(id="library"):
                    yield Label("Biblioteca", id="library-header", classes="view-header")
                    yield Label("", id="library-hint", classes="view-hint")
                    with Horizontal(classes="split"):
                        yield LibraryTree(id="lib-tree", classes="side-list")
                        with Vertical(id="lib-main"):
                            yield Input(placeholder="Filtrar por título, artista o álbum…",
                                        id="library-filter")
                            yield TrackTable("library", id="library-table")
                with Vertical(id="downloads"):
                    yield Label("Descargas", id="downloads-header", classes="view-header")
                    yield Label("", id="downloads-hint", classes="view-hint")
                    yield DownloadsTable(id="downloads-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="player"):
            yield CoverArt()
            with Vertical(id="player-main"):
                with Horizontal(id="now"):
                    with Vertical(id="now-text"):
                        yield Label("", id="now-title")
                        yield Label("", id="now-sub")
                    yield Static("", id="now-badges")
                yield Visualizer()
                yield SeekBar()
                with Horizontal(id="controls-row"):
                    yield Static("", id="controls")
                    yield Label("", id="now-status")
        yield Footer()

    # ================================================================ arranque
    def on_mount(self) -> None:
        for th in THEMES:
            self.register_theme(th)
        self.theme = self.cfg.theme if self.cfg.theme in self.available_themes else "beatshell"
        self.query_one(Visualizer).engine = self.engine
        self.query_one(CoverArt).pixel_style = self.cfg.pixel_covers
        self.query_one("#search-loading").display = False
        dt = self.query_one("#downloads-table", DataTable)
        dt.add_column(" ", key="icon", width=1)
        dt.add_column("Canción", key="song", width=44)
        dt.add_column("Destino", key="group", width=22)
        dt.add_column("Progreso", key="bar", width=18)
        dt.add_column("Estado", key="state", width=28)

        # La carpeta de descargas siempre está en la biblioteca.
        dl = Path(self.cfg.download_dir).expanduser()
        try:
            dl.mkdir(parents=True, exist_ok=True)
            if not any(os.path.normcase(f.path) == os.path.normcase(str(dl.resolve()))
                       for f in self.library.folders):
                self.library.add_folder(str(dl), "Descargas BeatShell")
        except OSError:
            pass

        tracks, idx = load_session()
        if tracks:
            self.queue.tracks = tracks
            self.queue.index = min(idx, len(tracks) - 1) if idx >= 0 else -1

        self._refresh_queue_view(force=True)
        self._refresh_library()
        self._refresh_downloads_header()
        self._set_now(self.queue.current)
        self._update_controls()
        self._update_sidebar_stats()
        self._on_size()
        self.set_interval(1 / 30, self._tick)
        self.query_one("#nav", OptionList).highlighted = 0
        self.query_one("#queue-table", TrackTable).focus()
        self._scan_library()

        if self._initial:
            text = self._initial
            if classify(text)[0] == "search" and not os.path.exists(text):
                text = "+" + text  # como un bot: reproduce el primer resultado
            self._submit(text)
        if self.engine_error:
            self.notify(self.engine_error, title="Sin audio", severity="error", timeout=15)
        elif not tracks and not self._initial:
            self.notify("Escribe arriba qué quieres escuchar y pulsa Enter. Pulsa ? para ver la ayuda.",
                        title="¡Bienvenido a BeatShell!", timeout=8)

    def on_resize(self, event: events.Resize) -> None:
        # Usamos el tamaño del evento: self.size todavía puede ser el anterior.
        self._on_size(event.size)

    def _on_size(self, size=None) -> None:  # noqa: ANN001
        size = size or self.size
        # Siempre sobre la pantalla principal, aunque haya un diálogo abierto encima.
        main = self.screen_stack[0] if self.screen_stack else self.screen
        main.set_class(size.height < 34, "-compact")
        pref = self.cfg.sidebar
        hidden = pref == "hide" or (pref == "auto" and size.width < 90)
        was_hidden = main.has_class("-narrow")
        main.set_class(hidden, "-narrow")
        if hidden and not was_hidden and pref == "auto" and not self._sidebar_hint_shown:
            self._sidebar_hint_shown = True
            self.notify("La ventana es estrecha y el menú se ha ocultado. "
                        "Pulsa Ctrl+B para mostrarlo.", title="Menú lateral", timeout=6)

    def action_toggle_sidebar(self) -> None:
        main = self.screen_stack[0] if self.screen_stack else self.screen
        visible = not main.has_class("-narrow")
        self.cfg.sidebar = "hide" if visible else "show"
        self.cfg.save()
        self._on_size()

    # ================================================================ bucle
    def _tick(self) -> None:
        eng = self.engine
        if eng is not None:
            if eng.poll_finished():
                self._on_track_end()
            seek = self.query_one(SeekBar)
            if eng.active:
                seek.position = eng.position
                seek.duration = eng.duration
            elif self._loading is None:
                seek.position = 0.0
                seek.duration = self.queue.current.duration if self.queue.current else None
            self.query_one(Visualizer).tick()
            if self.cfg.dj_mode:
                self._dj_tick(eng)
            if self._hq is not None and eng.is_fast and eng.upgrade_state != "pending":
                self._try_upgrade()
            self._update_status()
        if self.queue.version != self._queue_version_seen:
            self._refresh_queue_view()
        now = time.monotonic()
        if self.queue.version != self._session_saved_version and now - self._session_saved_at > 5:
            self._save_session()

    def _save_session(self) -> None:
        self._session_saved_version = self.queue.version
        self._session_saved_at = time.monotonic()
        try:
            save_session(self.queue.tracks, self.queue.index)
        except OSError:
            pass

    # ================================================================ reproducción
    def _start(self, track: Track | None, xfade: float = 0.0) -> None:
        self._load_token += 1
        token = self._load_token
        if self.engine is None:
            self.notify(self.engine_error or "Audio no disponible", severity="error")
            return
        if not (xfade > 0 and self.engine.active and not self.engine.paused):
            xfade = 0.0
            self.engine.stop()
        if track is None:
            self._loading = None
            self._set_now(None)
            return
        self._loading = track
        self._set_now(track)
        self._refresh_marks(track)
        self._load(track, token, xfade)

    @work(thread=True, group="load")
    def _load(self, track: Track, token: int, xfade: float) -> None:
        try:
            had_thumb = track.thumb
            src = self.resolver.resolve(track)
            self.call_from_thread(self._loaded, track, token, src, had_thumb != track.thumb, xfade)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._load_failed, track, token, str(exc))

    def _loaded(self, track: Track, token: int, src, thumb_changed: bool, xfade: float) -> None:  # noqa: ANN001
        if token != self._load_token or self.engine is None:
            return
        self._loading = None
        if xfade > 0 and self.engine.active:
            self.engine.crossfade_to(src, xfade)
        else:
            self.engine.play(src)
        self._hq, self._hq_tries = None, 0
        if src.fast:
            # Ya suena; en paralelo preparamos la alta calidad para cambiar sin cortes.
            self._await_hq(track, token)
        if thumb_changed:
            self.query_one(CoverArt).set_track(track)
        self._set_now(track, cover=False)
        self.resolver.prefetch(self.queue.peek_next())

    @work(thread=True, group="hq")
    def _await_hq(self, track: Track, token: int) -> None:
        try:
            src = self.resolver.hq_source(track)
        except Exception:  # noqa: BLE001 - seguimos con la calidad rápida
            return
        if src is not None:
            self.call_from_thread(self._hq_ready, token, src)

    def _hq_ready(self, token: int, src) -> None:  # noqa: ANN001
        if token != self._load_token or self.engine is None:
            return
        self._hq = (token, src)
        self._try_upgrade()

    def _try_upgrade(self) -> None:
        eng = self.engine
        if eng is None or self._hq is None or self._hq[0] != self._load_token or not eng.is_fast:
            return
        if eng.crossfading or eng.buffering:
            return  # se reintenta en el siguiente tick
        if self._hq_tries >= 4:
            return
        self._hq_tries += 1
        eng.upgrade(self._hq[1], lead=1.5 + self._hq_tries)

    def _load_failed(self, track: Track, token: int, error: str) -> None:
        if token != self._load_token:
            return
        self._loading = None
        self._fail_streak += 1
        self.notify(f"{track.title}: {error}", title="No se pudo reproducir", severity="error")
        if self._fail_streak < 3 and self.queue.peek_next() is not None:
            self.set_timer(0.8, self.action_next)
        else:
            self._fail_streak = 0
            self._set_now(track, cover=False)

    def _on_track_end(self) -> None:
        if self._loading is not None:
            return  # ya está cargando la siguiente (p. ej. un fundido en marcha)
        eng = self.engine
        if eng and eng.error:
            cur = self.queue.current
            self._load_failed(cur or Track(title="?"), self._load_token, eng.error)
            return
        self._fail_streak = 0
        nxt = self.queue.advance(auto=True)
        if nxt is None:
            self._set_now(self.queue.current, cover=False)
            self._update_status(force="■ Fin de la cola")
            return
        self._start(nxt)

    def play_list(self, tracks: list[Track], start: int) -> None:
        self._start(self.queue.replace(tracks, start))

    def play_now(self, track: Track) -> None:
        idx = self.queue.insert_next([track])
        self._start(self.queue.jump(idx))

    def enqueue(self, tracks: list[Track], next_: bool = False, quiet: bool = False) -> None:
        if not tracks:
            return
        idle = self._idle()
        if next_:
            self.queue.insert_next(tracks)
        else:
            self.queue.add(tracks)
        what = f"«{tracks[0].title}»" if len(tracks) == 1 else f"{len(tracks)} canciones"
        if idle:
            start = len(self.queue.tracks) - len(tracks) if not next_ else max(0, self.queue.index + 1)
            self._start(self.queue.jump(start))
            if not quiet:
                self.notify(f"Reproduciendo {what}", timeout=3)
        else:
            if not quiet:
                self.notify(f"{what} {'a continuación' if next_ else 'añadida(s) a la cola'}",
                            timeout=3)
            self.resolver.prefetch(self.queue.peek_next())

    def _idle(self) -> bool:
        return self.engine is None or (not self.engine.active and self._loading is None)

    # ----------------------------------------------------------- modo DJ
    def _dj_tick(self, eng: AudioEngine) -> None:
        cur = self.queue.current
        if (cur is None or not eng.active or eng.paused or eng.crossfading
                or self._loading is not None or eng.buffering):
            return
        dur, pos = eng.duration, eng.position
        if not dur or dur < 30:
            return
        remaining = dur - pos
        # Radio infinita: cuando se acaba la cola, añadimos canciones parecidas.
        if remaining < 75 and self.queue.peek_next() is None and self._radio_for != cur.uid:
            self._radio_for = cur.uid
            self._fetch_radio(cur)
        xf = min(float(self.cfg.dj_crossfade), dur / 4)
        if remaining <= xf and self._xfade_for != self._load_token:
            if self.queue.peek_next() is not None:
                self._xfade_for = self._load_token
                self._start(self.queue.advance(auto=True), xfade=max(1.0, remaining))

    @work(thread=True, group="radio", exclusive=True)
    def _fetch_radio(self, seed: Track) -> None:
        try:
            url = self.resolver.ensure_url(seed) if not seed.url else seed.url
            vid = youtube.youtube_id(url) if url else None
            if not vid:
                found = youtube.search(seed.search_query, 1)
                vid = youtube.youtube_id(found[0].url) if found else None
            if not vid:
                return
            col = youtube.playlist(f"https://www.youtube.com/watch?v={vid}&list=RD{vid}")
        except Exception:  # noqa: BLE001 - la radio es un extra: si falla, no molestamos
            return
        self.call_from_thread(self._radio_ready, col.tracks)

    def _radio_ready(self, tracks: list[Track]) -> None:
        known = {t.key for t in self.queue.tracks}
        known_titles = {t.title.lower() for t in self.queue.tracks}
        new = [t for t in tracks if t.key not in known and t.title.lower() not in known_titles
               and t.duration and t.duration < 900][:10]
        if new:
            self.enqueue(new, quiet=True)
            self.notify(f"Radio: {len(new)} canciones parecidas añadidas a la cola", title="Modo DJ",
                        timeout=4)

    def action_toggle_dj(self) -> None:
        self.cfg.dj_mode = not self.cfg.dj_mode
        self.cfg.save()
        self._apply_filters()
        self._update_controls()
        if self.cfg.dj_mode:
            self.notify(f"Fundidos de {self.cfg.dj_crossfade:.0f} s entre canciones, volumen nivelado "
                        "y radio infinita cuando se acabe la cola.", title="◉ Modo DJ activado",
                        timeout=5)
        else:
            self.notify("Modo DJ desactivado", timeout=2)

    # ----------------------------------------------------------- ecualizador
    def _apply_filters(self, preset: str | None = None) -> None:
        if self.engine is not None:
            self.engine.set_filters(eq.filter_chain(preset or self.cfg.eq_preset, self.cfg.dj_mode))

    def action_equalizer(self) -> None:
        previous = self.cfg.eq_preset

        def preview(name: str) -> None:
            self._apply_filters(name)

        def done(name: str | None) -> None:
            self.cfg.eq_preset = name or previous
            self.cfg.save()
            self._apply_filters()
            self._update_controls()
            if name:
                self.notify(f"Ecualizador: {name}", timeout=2)

        self.push_screen(EqualizerScreen(previous, preview), done)

    # ----------------------------------------------------------- acciones
    def action_toggle_pause(self) -> None:
        if self.engine is None:
            return
        if not self.engine.active and self._loading is None:
            cur = self.queue.current or self.queue.advance()
            if cur:
                self._start(cur)
            return
        self.engine.toggle_pause()
        self._update_status()

    def action_next(self) -> None:
        nxt = self.queue.advance()
        if nxt is not None:
            self._start(nxt, xfade=2.5 if self.cfg.dj_mode else 0.0)
        else:
            self.notify("No hay más canciones en la cola", timeout=2)

    def action_prev(self) -> None:
        if self.engine and self.engine.active and self.engine.position > 4:
            self.engine.seek(0)
            return
        prev = self.queue.back()
        if prev is not None:
            self._start(prev)

    def _seek_to(self, seconds: float) -> None:
        eng = self.engine
        if eng is None or not eng.active:
            return
        if eng.is_fast and self._hq is not None and self._hq[0] == self._load_token:
            # Si la alta calidad ya está lista, el salto la estrena directamente.
            dur = eng.duration
            target = max(0.0, min(seconds, dur - 0.5) if dur else seconds)
            eng.play(self._hq[1], start=target)
            return
        eng.seek(seconds)

    def action_seek(self, delta: float) -> None:
        if self.engine and self.engine.active:
            self._seek_to(self.engine.position + delta)

    @on(SeekBar.Seek)
    def _seek_click(self, event: SeekBar.Seek) -> None:
        self._seek_to(event.seconds)

    def action_volume(self, delta: int) -> None:
        if self.engine is None:
            return
        self._muted_volume = None
        self.engine.volume = self.engine.volume + delta
        self.cfg.volume = self.engine.volume
        self._update_controls()

    def action_vol_up(self) -> None:
        self.action_volume(5)

    def action_vol_down(self) -> None:
        self.action_volume(-5)

    def action_mute(self) -> None:
        if self.engine is None:
            return
        if self._muted_volume is None:
            self._muted_volume = self.engine.volume
            self.engine.volume = 0
        else:
            self.engine.volume = self._muted_volume
            self._muted_volume = None
        self._update_controls()

    def action_shuffle(self) -> None:
        self.queue.shuffle = not self.queue.shuffle
        self.cfg.shuffle = self.queue.shuffle
        self._update_controls()
        self.notify("Aleatorio activado" if self.queue.shuffle else "Aleatorio desactivado", timeout=2)
        self.resolver.prefetch(self.queue.peek_next())

    def action_repeat(self) -> None:
        mode = self.queue.cycle_repeat()
        self.cfg.repeat = mode
        self._update_controls()
        self.notify({"off": "Repetir: no", "all": "Repetir: toda la cola",
                     "one": "Repetir: esta canción"}[mode], timeout=2)

    def action_cycle_theme(self) -> None:
        names = [t.name for t in THEMES]
        i = names.index(self.theme) if self.theme in names else -1
        self.theme = names[(i + 1) % len(names)]

    def watch_theme(self, theme: str) -> None:
        if hasattr(self, "cfg"):
            self.cfg.theme = theme
            self._update_controls()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_focus_search(self) -> None:
        inp = self.query_one("#omnibar", Input)
        inp.focus()
        inp.action_select_all()

    def action_escape(self) -> None:
        if isinstance(self.focused, Input):
            self._focus_view_table()

    def action_view(self, name: str) -> None:
        self.query_one("#views", ContentSwitcher).current = name
        nav = self.query_one("#nav", OptionList)
        nav.highlighted = [k for k, _ in VIEWS].index(name)
        self._focus_view_table()

    def _focus_view_table(self) -> None:
        cur = self.query_one("#views", ContentSwitcher).current
        target = {"queue": "#queue-table", "search": "#search-table", "library": "#lib-tree",
                  "downloads": "#downloads-table"}.get(cur or "queue")
        if target:
            self.query_one(target).focus()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # Con una caja de texto enfocada, las letras se escriben; no son atajos.
        if isinstance(self.focused, Input) and action in TYPING_BLOCKED:
            return False
        if action in LIBRARY_ACTIONS:
            view = self.query_one("#views", ContentSwitcher).current if self.is_mounted else None
            return view == "library"
        return True

    async def action_quit(self) -> None:
        self._save_session()
        self.cfg.save()
        if self.engine:
            self.engine.close()
        self.downloader.shutdown()
        self.exit()

    def on_key(self, event) -> None:  # noqa: ANN001
        # 'x' sobre el árbol de la biblioteca quita el elemento seleccionado
        if event.key in ("x", "delete") and self.focused is self.query_one("#lib-tree"):
            event.stop()
            self._delete_selected()

    # ================================================================ estado visual
    def _set_now(self, track: Track | None, cover: bool = True) -> None:
        title = self.query_one("#now-title", Label)
        sub = self.query_one("#now-sub", Label)
        if track is None:
            title.update("Nada sonando")
            sub.update("Busca algo arriba ↑  o abre tu biblioteca (3)")
        else:
            title.update(Text(track.title, style="bold", no_wrap=True, overflow="ellipsis"))
            bits = [track.display_artist]
            if track.album:
                bits.append(track.album)
            sub.update(Text(" · ".join(bits), no_wrap=True, overflow="ellipsis"))
        if cover:
            self.query_one(CoverArt).set_track(track)
        self._update_status()

    def _update_status(self, force: str | None = None) -> None:
        eng = self.engine
        if force:
            status = force
        elif self.engine_error:
            status = "✖ sin dispositivo de audio"
        elif self._loading is not None:
            status = "◌ buscando la canción…" if not self._loading.url and not self._loading.path \
                else "◌ cargando…"
        elif eng is None or not eng.active:
            status = "■ detenido" if self.queue.current else ""
        elif eng.waiting > 0.2:
            status = f"◌ YouTube pide esperar {eng.waiting:.0f} s…"
        elif eng.buffering:
            status = "◌ cargando…"
        elif eng.crossfading:
            status = "⇋ fundido"
        elif eng.paused:
            status = "❚❚ en pausa"
        else:
            status = "▶ sonando"
        cur = self.queue.current
        if cur and status and not force:
            src = "local" if cur.is_local else ("YouTube" if cur.source != "spotify"
                                                else "Spotify → YouTube")
            status = f"{status}  ·  {src}  ·  {self.queue.index + 1}/{len(self.queue)}"
        if status != self._last_status:
            self._last_status = status
            self.query_one("#now-status", Label).update(status)
            self._update_controls()

    def _update_controls(self) -> None:
        if not self.is_mounted:
            return
        try:
            p = palette(self)
        except Exception:
            return
        on = art.hexc(p["accent"])
        off = art.hexc(p["dim"])
        eng = self.engine
        playing = eng is not None and eng.active and not eng.paused
        vol = eng.volume if eng else 0
        m = [
            "[b][@click=app.prev] ◀◀ [/][/]",
            f"[b {on}][@click=app.toggle_pause] {'❚❚' if playing else ' ▶'} [/][/]",
            "[b][@click=app.next] ▶▶ [/][/]",
            "    ",
            f"[{on if self.queue.shuffle else off}][@click=app.shuffle]⇄[/][/]  ",
            f"[{on if self.queue.repeat != 'off' else off}][@click=app.repeat]"
            f"{'↻¹' if self.queue.repeat == 'one' else '↻'}[/][/]",
            "    ",
        ]
        bars = 8
        filled = int(round(vol / 100 * bars))
        mute = self._muted_volume is not None
        m.append("[@click=app.vol_down]－[/] ")
        m.append(f"[{on}]{'▮' * filled}[/][{off}]{'▯' * (bars - filled)}[/]")
        m.append(f" [@click=app.vol_up]＋[/] [{off}]{'mute' if mute else f'{vol}%'}[/]")
        self.query_one("#controls", Static).update("".join(m))

        eq_on = self.cfg.eq_preset != eq.DEFAULT
        badges = (
            f"[{on if eq_on else off}][@click=app.equalizer] ≋ EQ {self.cfg.eq_preset} [/][/]"
            f"  [{on if self.cfg.dj_mode else off}][@click=app.toggle_dj] ◉ DJ [/][/]"
        )
        self.query_one("#now-badges", Static).update(badges)

    def _refresh_marks(self, track: Track | None) -> None:
        key = track.key if track else None
        for tid in ("#search-table", "#library-table"):
            tbl = self.query_one(tid, TrackTable)
            if tbl.playing_key != key:
                tbl.playing_key = key
                tbl.refresh_marks()

    def _update_sidebar_stats(self) -> None:
        active = sum(1 for j in self.downloader.jobs if not j.finished)
        lines = [
            f"♫ {len(self.queue)} en cola",
            f"▤ {self.library.count()} locales",
            f"☰ {len(self.playlists.items)} guardados",
        ]
        if active:
            lines.append(f"⇩ {active} descargando")
        self.query_one("#sidebar-stats", Static).update("\n".join(lines))

    # ================================================================ navegación
    @on(OptionList.OptionHighlighted, "#nav")
    def _nav_highlight(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self.query_one("#views", ContentSwitcher).current = event.option.id

    @on(OptionList.OptionSelected, "#nav")
    def _nav_select(self, event: OptionList.OptionSelected) -> None:
        self._focus_view_table()

    # ================================================================ cola
    def _refresh_queue_view(self, force: bool = False) -> None:
        self._queue_version_seen = self.queue.version
        tbl = self.query_one("#queue-table", TrackTable)
        tbl.current_index = self.queue.index
        if force or [t.uid for t in tbl.tracks] != [t.uid for t in self.queue.tracks]:
            tbl.set_tracks(self.queue.tracks)
        else:
            tbl.refresh_marks()
        total = sum(t.duration or 0 for t in self.queue.tracks)
        n = len(self.queue)
        self.query_one("#queue-header", Label).update(
            f"♫ Cola   {n} canci{'ón' if n == 1 else 'ones'} · {fmt_time(total)}"
            if n else "♫ Cola")
        self.query_one("#queue-hint", Label).update(
            "Enter reproducir · x quitar · Mayús↑↓ mover · G guardar como playlist · D descargar todo"
            if n else "La cola está vacía. Busca algo con / o elige canciones de tu biblioteca (3).")
        self._update_sidebar_stats()
        self._update_status()

    # ================================================================ búsqueda
    @on(Input.Submitted, "#omnibar")
    def _omnibar(self, event: Input.Submitted) -> None:
        if self._submit(event.value.strip()):
            event.input.value = ""

    def _submit(self, text: str) -> bool:
        """Procesa lo escrito en la barra. Devuelve True si hay que vaciarla."""
        if not text:
            return False
        # Rutas locales (también cuando se arrastra un archivo a la terminal)
        candidate = text.strip("'\"")
        if candidate.startswith("~") or os.path.isabs(candidate) or os.path.exists(candidate):
            path = Path(candidate).expanduser()
            if path.is_dir():
                self._add_folder(str(path))
                return True
            if path.is_file() and path.suffix.lower() in (".m3u", ".m3u8"):
                self._import_playlist(str(path))
                return True
            if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
                self.enqueue([track_from_file(path)])
                return True
        quick = text.startswith("+")
        if quick:
            text = text[1:].strip()
        kind, value = classify(text)
        if kind == "search":
            self._show_loading(f"Buscando «{value}» en YouTube…")
            self._do_search(value, quick)
            return False
        if kind == "youtube_playlist":
            self._show_loading("Leyendo la playlist de YouTube…")
            self._do_collection("youtube", value)
        elif kind == "spotify":
            self._show_loading("Leyendo Spotify…", switch=not value.startswith("track:"))
            self._do_collection("spotify", value)
        else:  # vídeo de YouTube u otra web compatible con yt-dlp
            self._show_loading("Leyendo el enlace…", switch=False)
            self._do_video(value)
        return True

    def _show_loading(self, message: str, switch: bool = True) -> None:
        if switch:
            self.action_view("search")
            self.query_one("#search-header", Label).update(message)
            self.query_one("#search-hint", Label).update("")
            self.query_one("#search-loading").display = True
            self.query_one("#search-table").display = False
        else:
            self.notify(message, timeout=2)

    def _hide_loading(self) -> None:
        self.query_one("#search-loading").display = False
        self.query_one("#search-table").display = True

    @work(thread=True, group="search", exclusive=True)
    def _do_search(self, query: str, quick: bool) -> None:
        try:
            tracks = youtube.search(query, 20)
            col = Collection(name=query, tracks=tracks, kind="search", source="youtube")
            self.call_from_thread(self._show_results, col, quick)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._search_failed, str(exc))

    @work(thread=True, group="search", exclusive=True)
    def _do_collection(self, source: str, ref: str) -> None:
        try:
            if source == "spotify":
                col = spotify.fetch(ref, self.cfg.spotify_client_id, self.cfg.spotify_client_secret)
            else:
                col = youtube.playlist(ref)
            if not col.tracks:
                raise RuntimeError("No tiene canciones disponibles")
            if ref.startswith("track:"):
                # Una canción suelta se comporta como un enlace de YouTube: suena o va a la cola.
                self.call_from_thread(self.enqueue, [col.tracks[0]])
                return
            self.call_from_thread(self._show_results, col, False)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._search_failed, str(exc))

    @work(thread=True, group="video")
    def _do_video(self, url: str) -> None:
        try:
            t = youtube.video_track(url)
            self.call_from_thread(self.enqueue, [t])
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), title="No se pudo abrir", severity="error")

    def _search_failed(self, error: str) -> None:
        self._hide_loading()
        self.notify(error, title="No se pudo abrir", severity="error")
        self.query_one("#search-header", Label).update("✖ " + error)
        self.query_one("#search-hint", Label).update("Revisa la conexión o prueba otra búsqueda.")

    def _show_results(self, col: Collection, quick: bool) -> None:
        self._hide_loading()
        self._results = col
        tbl = self.query_one("#search-table", TrackTable)
        tbl.set_tracks(col.tracks, keep_cursor=False)
        tbl.playing_key = self.queue.current.key if self.queue.current else None
        tbl.refresh_marks()
        n = len(col.tracks)
        if col.kind == "search":
            header = f"⌕ «{col.name}»   {n} resultados de YouTube"
            hint = ("Enter reproducir · a cola · i a continuación · "
                    "g guardar (playlist, álbum o artista) · d descargar MP3")
        else:
            icon = "●" if col.source == "spotify" else "▶"
            src = "Spotify" if col.source == "spotify" else "YouTube"
            kind = "Álbum" if col.kind == "album" else "Playlist"
            dur = sum(t.duration or 0 for t in col.tracks)
            header = f"{icon} {kind} de {src} · {col.name}"
            if col.owner:
                header += f" — {col.owner}"
            header += f"   {n} canciones · {fmt_time(dur)}"
            hint = (f"Enter reproducir desde aquí · A todo a la cola · "
                    f"G guardar {'el álbum' if col.kind == 'album' else 'la playlist'} "
                    "en tu biblioteca · D descargar todo en MP3")
            if (col.source == "spotify" and n >= spotify.EMBED_LIMIT
                    and not self.cfg.spotify_client_id):
                self.notify("Spotify solo muestra las primeras 100 canciones sin credenciales de API. "
                            "Mira el README para importar playlists completas.",
                            severity="warning", timeout=10)
        self.query_one("#search-header", Label).update(header)
        self.query_one("#search-hint", Label).update(hint)
        if self.query_one("#views", ContentSwitcher).current == "search":
            tbl.focus()
        if quick and col.tracks:
            self.enqueue([col.tracks[0]])
        else:
            # Pre-resolvemos los primeros resultados: suelen ser los que se eligen.
            for t in col.tracks[:3]:
                self.resolver.prefetch(t)

    @on(DataTable.RowHighlighted)
    def _row_highlight(self, event: DataTable.RowHighlighted) -> None:
        # Al detenerse sobre una canción de internet, la preparamos para que empiece al instante.
        tbl = event.data_table
        if not isinstance(tbl, TrackTable):
            return
        t = tbl.selected
        if t is None or t.is_local:
            return
        if self._prefetch_timer is not None:
            self._prefetch_timer.stop()
        self._prefetch_timer = self.set_timer(0.35, lambda: self.resolver.prefetch(t))

    # ================================================================ acciones de tabla
    @on(TrackTable.Action)
    def _table_action(self, event: TrackTable.Action) -> None:
        tbl, act, i = event.table, event.action, event.index
        tracks = tbl.tracks
        if not tracks or not (0 <= i < len(tracks)):
            return
        track = tracks[i]
        ctx = tbl.context
        item = self._sel_item if tbl.id == "library-table" else None
        if act == "play":
            if ctx == "queue":
                self._start(self.queue.jump(i))
            elif ctx == "search" and self._results and self._results.kind == "search":
                self.play_now(track)
            else:
                self.play_list(tracks, i)
        elif act == "enqueue":
            self.enqueue([track])
        elif act == "play_next":
            self.enqueue([track], next_=True)
        elif act == "enqueue_all":
            self.enqueue(tracks)
        elif act == "save":
            self._open_save_menu([track], f"«{track.title}»", single=track)
        elif act == "save_all":
            self._save_all(tbl)
        elif act == "download":
            self._download([track], self._group_for(tbl))
        elif act == "download_all":
            self._download_all(tbl)
        elif act == "remove":
            if ctx == "queue":
                was_current = i == self.queue.index
                self.queue.remove(i)
                if was_current:
                    self.action_next() if self.queue.peek_next() else self._start(None)
            elif ctx == "playlist" and item is not None:
                self.playlists.remove(item.id, track.uid)
                self._refresh_library()
        elif act == "move":
            if ctx == "queue":
                j = self.queue.move(i, event.arg)
                self._refresh_queue_view(force=True)
                tbl.move_cursor(row=j)
            elif ctx == "playlist" and item is not None:
                j = self.playlists.move(item.id, i, event.arg)
                self._show_library_selection()
                tbl.move_cursor(row=j)

    def _group_for(self, tbl: TrackTable) -> str | None:
        if tbl.id == "library-table" and (item := self._sel_item) is not None:
            return item.name
        if tbl.context == "search" and self._results and self._results.kind != "search":
            return self._results.name
        return None

    def _save_all(self, tbl: TrackTable) -> None:
        tracks = tbl.tracks
        if tbl.context == "search" and self._results and self._results.kind != "search":
            col = self._results
            before = len(self.playlists.items)
            p = self._save_collection(col)
            if len(self.playlists.items) == before:
                self.notify(f"Ya está en tu biblioteca como «{p.name}»", timeout=4)
            else:
                kind = "Álbum" if col.kind == "album" else "Playlist"
                self.notify(f"{kind} «{p.name}» guardado en tu biblioteca ({len(tracks)} canciones)")
            return
        what = "la cola" if tbl.context == "queue" else f"{len(tracks)} canciones"
        self._open_save_menu(tracks, what)

    # ================================================================ biblioteca local
    def action_add_folder(self) -> None:
        def chosen(path: str | None) -> None:
            if path:
                self._add_folder(path)
        self.push_screen(TextPrompt(
            "Añadir carpeta de música", "/ruta/a/tu/música",
            hint="Consejo: puedes arrastrar la carpeta a la terminal para pegar su ruta."), chosen)

    def _add_folder(self, path: str) -> None:
        p = Path(path.strip().strip("'\"")).expanduser()
        if not p.is_dir():
            self.notify(f"No existe la carpeta {p}", severity="error")
            return
        f = self.library.add_folder(str(p))
        self.notify(f"«{f.name}» añadida a la biblioteca. Escaneando…")
        self._lib_sel = ("dir", f.id)
        self._lib_expanded.add("__local__")
        self.action_view("library")
        self._refresh_library()
        self._scan_library()

    def action_rescan(self) -> None:
        self.notify("Escaneando biblioteca…", timeout=2)
        self._scan_library()

    @work(thread=True, group="scan", exclusive=True)
    def _scan_library(self) -> None:
        def progress(count: int, name: str) -> None:
            self.call_from_thread(self.query_one("#library-hint", Label).update,
                                  f"Escaneando… {count} archivos · {name}")
        try:
            n = self.library.scan(progress)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Error escaneando: {exc}", severity="error")
            return
        self.call_from_thread(self._scan_done, n)

    def _scan_done(self, n: int) -> None:
        self._refresh_library()
        self._update_sidebar_stats()

    # ================================================================ descargas
    def _download(self, tracks: list[Track], group: str | None) -> None:
        todo = [t for t in tracks if not t.is_local]
        if not todo:
            self.notify("Ya está en tu biblioteca local", timeout=3)
            return
        jobs = self.downloader.enqueue(todo, group)
        if jobs:
            name = jobs[0].group
            self._groups.setdefault(name, []).extend(j.id for j in jobs)
            what = f"«{todo[0].title}»" if len(jobs) == 1 else f"{len(jobs)} canciones"
            self.notify(f"Descargando {what} en MP3 → {name}", timeout=4)
        self._refresh_downloads_header()

    def _download_all(self, tbl: TrackTable) -> None:
        tracks = tbl.tracks
        group = self._group_for(tbl)
        if tbl.context == "search" and self._results and self._results.kind != "search":
            # Una playlist/álbum que se descarga se guarda también en la biblioteca,
            # y sus canciones pasan a apuntar a los MP3 cuando terminan.
            p = self._save_collection(self._results)
            group = p.name
        elif tbl.context == "queue":
            group = None
        self._download(tracks, group)

    def _from_downloader(self, job: DownloadJob) -> None:
        if threading.get_ident() == self._main_thread:
            self._job_changed(job)
        else:
            self.call_from_thread(self._job_changed, job)

    def _job_changed(self, job: DownloadJob) -> None:
        dt = self.query_one("#downloads-table", DataTable)
        t = job.track
        song = f"{t.artist} - {t.title}" if t.artist else t.title
        icon = {"listo": "✔", "ya existía": "✔", "error": "✖"}.get(job.status, "⇩")
        state = job.error if job.status == FAILED else job.stage
        color = art.hexc(palette(self)["error" if job.status == FAILED else "primary"])
        cells = (icon, song[:44], job.group[:22], _bar(job.progress, color), state[:28])
        if job.id in dt.rows:
            for col, val in zip(("icon", "song", "group", "bar", "state"), cells):
                dt.update_cell(job.id, col, val)
        else:
            dt.add_row(*cells, key=job.id)
        if job.finished and job.path:
            self.playlists.link_local(job.key, job.path)
        if job.finished:
            self._check_group_done(job.group)
        self._refresh_downloads_header()
        self._update_sidebar_stats()

    def _check_group_done(self, group: str) -> None:
        ids = self._groups.get(group)
        if not ids:
            return
        jobs = [j for j in self.downloader.jobs if j.id in ids]
        if jobs and all(j.finished for j in jobs):
            ok = sum(1 for j in jobs if j.status != FAILED)
            bad = len(jobs) - ok
            msg = f"«{group}»: {ok} canciones listas"
            if bad:
                msg += f", {bad} con error (pulsa 4 para verlas)"
            self.notify(msg, title="Descarga terminada", severity="warning" if bad else "information",
                        timeout=8)
            self._refresh_library()
            del self._groups[group]

    def action_clear_downloads(self) -> None:
        self.downloader.clear_finished()
        dt = self.query_one("#downloads-table", DataTable)
        keep = {j.id for j in self.downloader.jobs}
        for key in [k.value for k in list(dt.rows) if k.value not in keep]:
            dt.remove_row(key)
        self._refresh_downloads_header()

    def action_retry_downloads(self) -> None:
        n = self.downloader.retry_failed()
        self.notify(f"Reintentando {n} descarga(s)" if n else "No hay descargas fallidas", timeout=3)

    def action_open_downloads(self) -> None:
        import subprocess
        import sys

        path = str(Path(self.cfg.download_dir).expanduser())
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"No se pudo abrir {path}: {exc}", severity="error")

    def _refresh_downloads_header(self) -> None:
        jobs = self.downloader.jobs
        active = sum(1 for j in jobs if not j.finished)
        done = sum(1 for j in jobs if j.finished and j.status != FAILED)
        failed = sum(1 for j in jobs if j.status == FAILED)
        self.query_one("#downloads-header", Label).update(
            f"⇩ Descargas   {active} activas · {done} listas" + (f" · {failed} con error" if failed else ""))
        self.query_one("#downloads-hint", Label).update(
            f"{self.cfg.download_dir} · MP3 "
            f"{'V0 (~245 kbps)' if self.cfg.mp3_quality == '0' else self.cfg.mp3_quality + ' kbps'}"
            " con portada · c limpiar · y reintentar · o abrir carpeta")
