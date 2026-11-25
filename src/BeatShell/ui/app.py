"""Aplicación principal de BeatShell (Textual)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
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

from .. import art
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
from .screens import NEW_PLAYLIST, Confirm, HelpScreen, PlaylistPicker, TextPrompt
from .widgets import (
    CoverArt,
    DownloadsTable,
    Logo,
    SeekBar,
    TrackTable,
    Visualizer,
    fold,
    palette,
)

THEMES = [
    Theme(
        name="beatshell", primary="#ff4fd8", secondary="#5ee7ff", accent="#ffd166",
        foreground="#ece6ff", background="#0f0b1e", surface="#171230", panel="#221a44",
        success="#7cf29a", warning="#ffd166", error="#ff5c7a", dark=True,
    ),
    Theme(
        name="beatshell-gameboy", primary="#9bbc0f", secondary="#8bac0f", accent="#e0f8d0",
        foreground="#c4dc8c", background="#0f1f0f", surface="#173017", panel="#306230",
        success="#9bbc0f", warning="#e0f8d0", error="#f87858", dark=True,
    ),
    Theme(
        name="beatshell-sunset", primary="#ff8a3d", secondary="#ff4f6d", accent="#ffe066",
        foreground="#fff1e0", background="#1a0f12", surface="#26151a", panel="#3a1e24",
        success="#a7f070", warning="#ffe066", error="#ff4f6d", dark=True,
    ),
    Theme(
        name="beatshell-ice", primary="#5ab8ff", secondary="#a78bfa", accent="#7ff0d8",
        foreground="#e6f1ff", background="#0a1020", surface="#111a30", panel="#1a2644",
        success="#7ff0d8", warning="#ffd27f", error="#ff7a90", dark=True,
    ),
]

VIEWS = [
    ("queue", "♫  Cola"),
    ("search", "⌕  Buscar"),
    ("library", "▤  Biblioteca"),
    ("playlists", "☰  Playlists"),
    ("downloads", "⇩  Descargas"),
]

ALL_FOLDERS = "__all__"


def _two_lines(title: str, sub: str) -> Text:
    t = Text(title)
    t.append("\n  " + sub, style="dim")
    return t


def _bar(frac: float, color: str, width: int = 12) -> Text:
    filled = int(round(frac * width))
    t = Text(no_wrap=True)
    t.append("▰" * filled, style=color)
    t.append("▱" * (width - filled), style="dim")
    t.append(f" {int(frac * 100):>3}%")
    return t


class BeatShellApp(App):
    TITLE = "BeatShell"
    CSS_PATH = "beatshell.tcss"
    ENABLE_COMMAND_PALETTE = True

    BINDINGS = [
        Binding("space", "toggle_pause", "Play/Pausa", key_display="␣"),
        Binding("n", "next", "Siguiente"),
        Binding("b", "prev", "Ant.", show=False),
        Binding("plus,equals_sign", "volume(5)", "Volumen", key_display="+/−"),
        Binding("minus", "volume(-5)", "Vol−", key_display="−", show=False),
        Binding("m", "mute", "Silencio", show=False),
        Binding("s", "shuffle", "Aleatorio", show=False),
        Binding("r", "repeat", "Repetir", show=False),
        Binding("slash", "focus_search", "Buscar", key_display="/"),
        Binding("question_mark", "help", "Ayuda", key_display="?"),
        Binding("t", "cycle_theme", "Tema", show=False),
        Binding("q", "quit", "Salir"),
        Binding("1", "view('queue')", "Cola", show=False),
        Binding("2", "view('search')", "Buscar", show=False),
        Binding("3", "view('library')", "Biblioteca", show=False),
        Binding("4", "view('playlists')", "Playlists", show=False),
        Binding("5", "view('downloads')", "Descargas", show=False),
        Binding("f", "add_folder", "Añadir carpeta", show=False),
        Binding("R", "rescan", "Re-escanear", show=False),
        Binding("N", "new_playlist", "Nueva playlist", show=False),
        Binding("e", "rename", "Renombrar", show=False),
        Binding("escape", "escape", show=False),
    ]

    def __init__(self, initial: str | None = None) -> None:
        super().__init__()
        self._initial = initial
        self.cfg = Config.load()
        self.library = Library()
        self.playlists = PlaylistStore()
        self.matches = MatchCache()
        self.resolver = Resolver(self.matches)
        self.queue = PlayQueue()
        self.queue.repeat = self.cfg.repeat
        self.queue.shuffle = self.cfg.shuffle
        self.engine: AudioEngine | None = None
        self.engine_error: str | None = None
        try:
            self.engine = AudioEngine(volume=self.cfg.volume)
        except (AudioError, Exception) as exc:  # noqa: BLE001
            self.engine_error = str(exc)
        self.downloader = Downloader(
            self.cfg, self.resolver, self.library,
            self._from_downloader,
        )
        self._main_thread = threading.get_ident()
        self._load_token = 0
        self._loading: Track | None = None
        self._fail_streak = 0
        self._muted_volume: int | None = None
        self._queue_version_seen = -1
        self._session_saved_version = -1
        self._session_saved_at = 0.0
        self._results: Collection | None = None
        self._library_folder = ALL_FOLDERS
        self._library_tracks: list[Track] = []
        self._playlist_id: str | None = None
        self._last_status = ""
        self._groups: dict[str, list[str]] = {}
        self._prefetch_key: str | None = None
        self._prefetch_timer = None

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
                        yield OptionList(id="folders", classes="side-list")
                        with Vertical():
                            yield Input(placeholder="Filtrar por título, artista o álbum…",
                                        id="library-filter")
                            yield TrackTable("library", id="library-table")
                with Vertical(id="playlists"):
                    yield Label("Playlists", id="playlists-header", classes="view-header")
                    yield Label("", id="playlists-hint", classes="view-hint")
                    with Horizontal(classes="split"):
                        yield OptionList(id="playlist-list", classes="side-list")
                        yield TrackTable("playlist", id="playlist-table")
                with Vertical(id="downloads"):
                    yield Label("Descargas", id="downloads-header", classes="view-header")
                    yield Label("", id="downloads-hint", classes="view-hint")
                    yield DownloadsTable(id="downloads-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="player"):
            yield CoverArt()
            with Vertical(id="player-main"):
                yield Label("", id="now-title")
                yield Label("", id="now-sub")
                yield Label("", id="now-status")
                yield Visualizer()
                yield SeekBar()
                yield Static("", id="controls")
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

        # La carpeta de descargas siempre es una biblioteca.
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
        self._refresh_folders()
        self._refresh_library_table()
        self._refresh_playlists()
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

    def on_resize(self) -> None:
        self._on_size()

    def _on_size(self) -> None:
        self.screen.set_class(self.size.height < 34, "-compact")
        self.screen.set_class(self.size.width < 90, "-narrow")

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
    def _start(self, track: Track | None) -> None:
        self._load_token += 1
        token = self._load_token
        if self.engine is None:
            self.notify(self.engine_error or "Audio no disponible", severity="error")
            return
        self.engine.stop()
        if track is None:
            self._loading = None
            self._set_now(None)
            return
        self._loading = track
        self._set_now(track)
        self._refresh_marks(track)
        self._load(track, token)

    @work(thread=True, group="load")
    def _load(self, track: Track, token: int) -> None:
        try:
            had_thumb = track.thumb
            src = self.resolver.resolve(track)
            self.call_from_thread(self._loaded, track, token, src, had_thumb != track.thumb)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._load_failed, track, token, str(exc))

    def _loaded(self, track: Track, token: int, src, thumb_changed: bool) -> None:
        if token != self._load_token or self.engine is None:
            return
        self._loading = None
        self.engine.play(src)
        if thumb_changed:
            self.query_one(CoverArt).set_track(track)
        self._set_now(track, cover=False)
        self.resolver.prefetch(self.queue.peek_next())

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
        eng = self.engine
        if eng and eng.error and eng.position < 1:
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

    def enqueue(self, tracks: list[Track], next_: bool = False) -> None:
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
            self.notify(f"Reproduciendo {what}", timeout=3)
        else:
            self.notify(f"{what} {'a continuación' if next_ else 'añadida(s) a la cola'}", timeout=3)
            self.resolver.prefetch(self.queue.peek_next())

    def _idle(self) -> bool:
        return self.engine is None or (not self.engine.active and self._loading is None)

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
            self._start(nxt)
        else:
            self.notify("No hay más canciones en la cola", timeout=2)

    def action_prev(self) -> None:
        if self.engine and self.engine.active and self.engine.position > 4:
            self.engine.seek(0)
            return
        prev = self.queue.back()
        if prev is not None:
            self._start(prev)

    def action_seek(self, delta: float) -> None:
        if self.engine and self.engine.active:
            self.engine.seek_relative(delta)

    @on(SeekBar.Seek)
    def _seek_click(self, event: SeekBar.Seek) -> None:
        if self.engine and self.engine.active:
            self.engine.seek(event.seconds)

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
        focused = self.focused
        if isinstance(focused, Input):
            self._focus_view_table()

    def action_view(self, name: str) -> None:
        self.query_one("#views", ContentSwitcher).current = name
        nav = self.query_one("#nav", OptionList)
        nav.highlighted = [k for k, _ in VIEWS].index(name)
        self._focus_view_table()
        if name == "library":
            self._refresh_library_table()

    def _focus_view_table(self) -> None:
        cur = self.query_one("#views", ContentSwitcher).current
        target = {"queue": "#queue-table", "search": "#search-table", "library": "#library-table",
                  "playlists": "#playlist-list", "downloads": "#downloads-table"}.get(cur or "queue")
        if cur == "playlists" and self.query_one("#playlist-table", TrackTable).tracks:
            target = "#playlist-table"
        if target:
            self.query_one(target).focus()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # Con una caja de texto enfocada, las letras se escriben; solo interceptamos atajos.
        if isinstance(self.focused, Input) and action not in ("escape", "quit", "help", "view"):
            if action in ("toggle_pause", "next", "prev", "volume", "mute", "shuffle", "repeat",
                          "cycle_theme", "focus_search", "add_folder", "rescan",
                          "new_playlist", "rename"):
                return False
        view = self.query_one("#views", ContentSwitcher).current if self.is_mounted else None
        if action in ("add_folder", "rescan"):
            return view == "library"
        if action == "new_playlist":
            return view == "playlists"
        if action == "rename":
            return view in ("library", "playlists")
        return True

    async def action_quit(self) -> None:
        self._save_session()
        self.cfg.save()
        if self.engine:
            self.engine.close()
        self.downloader.shutdown()
        self.exit()

    # ================================================================ estado visual
    def _set_now(self, track: Track | None, cover: bool = True) -> None:
        title = self.query_one("#now-title", Label)
        sub = self.query_one("#now-sub", Label)
        if track is None:
            title.update("Nada sonando")
            sub.update("Busca algo arriba ↑  o abre tu biblioteca (3)")
        else:
            title.update(Text(track.title, style="bold"))
            bits = [track.display_artist]
            if track.album:
                bits.append(track.album)
            sub.update(" · ".join(bits))
        if cover:
            self.query_one(CoverArt).set_track(track)
        self._update_status()

    def _update_status(self, force: str | None = None) -> None:
        eng = self.engine
        if force:
            status = force
        elif self.engine_error:
            status = "✖ Sin dispositivo de audio"
        elif self._loading is not None:
            status = "◌ Buscando la canción…" if not self._loading.url and not self._loading.path \
                else "◌ Cargando…"
        elif eng is None or not eng.active:
            status = "■ Detenido" if self.queue.current else ""
        elif eng.waiting > 0.2:
            status = f"◌ YouTube pide esperar {eng.waiting:.0f} s antes de servir el audio…"
        elif eng.buffering:
            status = "◌ Cargando…"
        elif eng.paused:
            status = "❚❚ En pausa"
        else:
            status = "▶ Reproduciendo"
        cur = self.queue.current
        if cur and status and not force:
            src = "archivo local" if cur.is_local else ("YouTube" if cur.source != "spotify"
                                                         else "Spotify → YouTube")
            status = f"{status}   ·   {src}"
            pos = self.queue.index + 1
            status += f"   ·   {pos}/{len(self.queue)}"
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
        parts = [
            ("◀◀", "prev", "bold"),
            ("❚❚" if playing else " ▶", "toggle_pause", f"bold {on}"),
            ("▶▶", "next", "bold"),
        ]
        markup = []
        for label, action, style in parts:
            markup.append(f"[{style}][@click=app.{action}] {label} [/][/]")
        markup.append("   ")
        markup.append(f"[{on if self.queue.shuffle else off}][@click=app.shuffle]⇄ aleatorio[/][/]")
        markup.append("   ")
        rep = {"off": "↻ repetir", "all": "↻ repetir todo", "one": "↻ repetir una"}[self.queue.repeat]
        markup.append(f"[{on if self.queue.repeat != 'off' else off}][@click=app.repeat]{rep}[/][/]")
        markup.append("   ")
        bars = 10
        filled = int(round(vol / 100 * bars))
        mute = self._muted_volume is not None
        markup.append("[@click=app.vol_down]◖[/] ")
        markup.append(f"[{on}]{'▮' * filled}[/][{off}]{'▯' * (bars - filled)}[/]")
        markup.append(f" [@click=app.vol_up]◗[/]  {'silencio' if mute else f'{vol}%'}")
        self.query_one("#controls", Static).update("".join(markup))

    def _refresh_marks(self, track: Track | None) -> None:
        key = track.key if track else None
        for tid in ("#search-table", "#library-table", "#playlist-table"):
            tbl = self.query_one(tid, TrackTable)
            if tbl.playing_key != key:
                tbl.playing_key = key
                tbl.refresh_marks()

    def _update_sidebar_stats(self) -> None:
        active = sum(1 for j in self.downloader.jobs if not j.finished)
        lines = [
            f"♫ {len(self.queue)} en cola",
            f"▤ {self.library.count()} locales",
            f"☰ {len(self.playlists.items)} playlists",
        ]
        if active:
            lines.append(f"⇩ {active} descargando")
        self.query_one("#sidebar-stats", Static).update("\n".join(lines))

    # ================================================================ navegación
    @on(OptionList.OptionHighlighted, "#nav")
    def _nav_highlight(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self.query_one("#views", ContentSwitcher).current = event.option.id
            if event.option.id == "library":
                self._refresh_library_table()

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
            f"♫ Cola de reproducción   {n} canci{'ón' if n == 1 else 'ones'} · {fmt_time(total)}"
            if n else "♫ Cola de reproducción")
        self.query_one("#queue-hint", Label).update(
            "Enter reproducir · x quitar · Mayús+↑↓ mover · G guardar como playlist · D descargar todo"
            if n else "La cola está vacía. Busca algo con / o elige canciones de tu biblioteca.")
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
                self.call_from_thread(self._single_link_done, col.tracks[0])
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

    def _single_link_done(self, track: Track) -> None:
        self.enqueue([track])

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
            header = f"⌕ Resultados para «{col.name}»   {n} vídeos de YouTube"
            hint = ("Enter reproducir · a añadir a la cola · i a continuación · "
                    "g guardar en playlist · d descargar MP3")
        else:
            icon = "●" if col.source == "spotify" else "▶"
            src = "Spotify" if col.source == "spotify" else "YouTube"
            kind = "Álbum" if col.kind == "album" else "Playlist"
            dur = sum(t.duration or 0 for t in col.tracks)
            header = f"{icon} {kind} de {src}: {col.name}"
            if col.owner:
                header += f" — {col.owner}"
            header += f"   {n} canciones · {fmt_time(dur)}"
            hint = ("Enter reproducir desde aquí · A añadir todo a la cola · "
                    "G guardar como playlist · D descargar todo en MP3")
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
        elif col.tracks:
            # Pre-resolvemos el primer resultado: suele ser el que se elige.
            self.resolver.prefetch(col.tracks[0])

    @on(DataTable.RowHighlighted, "#search-table")
    def _search_highlight(self, event: DataTable.RowHighlighted) -> None:
        # Al detenerse sobre un resultado, lo preparamos para que empiece al instante.
        tbl = self.query_one("#search-table", TrackTable)
        t = tbl.selected
        if t is None or self._results is None:
            return
        if self._prefetch_timer is not None:
            self._prefetch_timer.stop()
        self._prefetch_timer = self.set_timer(0.7, lambda: self.resolver.prefetch(t))

    # ================================================================ acciones de tabla
    @on(TrackTable.Action)
    def _table_action(self, event: TrackTable.Action) -> None:
        tbl, act, i = event.table, event.action, event.index
        tracks = tbl.tracks
        if not tracks or not (0 <= i < len(tracks)):
            return
        track = tracks[i]
        ctx = tbl.context
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
            self._save_to_playlist([track], f"«{track.title}»")
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
            elif ctx == "playlist" and self._playlist_id:
                self.playlists.remove(self._playlist_id, track.uid)
                self._show_playlist(self._playlist_id)
                self._refresh_playlists(keep=True)
        elif act == "move":
            if ctx == "queue":
                j = self.queue.move(i, event.arg)
                self._refresh_queue_view(force=True)
                tbl.move_cursor(row=j)
            elif ctx == "playlist" and self._playlist_id:
                j = self.playlists.move(self._playlist_id, i, event.arg)
                self._show_playlist(self._playlist_id)
                tbl.move_cursor(row=j)

    def _group_for(self, tbl: TrackTable) -> str | None:
        if tbl.context == "playlist" and self._playlist_id:
            p = self.playlists.get(self._playlist_id)
            return p.name if p else None
        if tbl.context == "search" and self._results and self._results.kind != "search":
            return self._results.name
        return None

    # ================================================================ playlists
    def _save_to_playlist(self, tracks: list[Track], what: str) -> None:
        def chosen(pid: str | None) -> None:
            if pid is None:
                return
            if pid == NEW_PLAYLIST:
                def named(name: str | None) -> None:
                    if name:
                        p = self.playlists.create(name, tracks)
                        self.notify(f"Playlist «{p.name}» creada con {len(tracks)} canción(es)")
                        self._refresh_playlists(keep=True)
                self.push_screen(TextPrompt("Nombre de la nueva playlist", "Mi playlist"), named)
                return
            n = self.playlists.add(pid, tracks)
            p = self.playlists.get(pid)
            self.notify(f"{n} canción(es) añadidas a «{p.name if p else ''}»", timeout=3)
            self._refresh_playlists(keep=True)
            if self._playlist_id == pid:
                self._show_playlist(pid)

        self.push_screen(PlaylistPicker(self.playlists.items, what), chosen)

    def _save_all(self, tbl: TrackTable) -> None:
        tracks = tbl.tracks
        if tbl.context == "search" and self._results and self._results.kind != "search":
            col = self._results
            existing = next((p for p in self.playlists.items
                             if col.url and p.source_url == col.url), None)
            if existing:
                self.notify(f"Ya la tienes guardada como «{existing.name}»", timeout=4)
                return
            p = self.playlists.create(col.name, tracks, source_url=col.url)
            self.notify(f"Guardada como playlist «{p.name}» ({len(tracks)} canciones)")
            self._refresh_playlists(keep=True)
            return
        what = "la cola" if tbl.context == "queue" else f"{len(tracks)} canciones"
        self._save_to_playlist(tracks, what)

    def _refresh_playlists(self, keep: bool = False) -> None:
        ol = self.query_one("#playlist-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        for p in self.playlists.items:
            n = len(p.tracks)
            local = sum(1 for t in p.tracks if t.is_local)
            badge = " ⇩" if n and local == n else ""
            ol.add_option(Option(_two_lines(f"☰ {p.name}",
                                            f"{n} canciones · {fmt_time(p.duration)}{badge}"),
                                 id=p.id))
        if self.playlists.items:
            idx = prev if keep and prev is not None else 0
            ol.highlighted = min(idx, len(self.playlists.items) - 1)
        else:
            self._playlist_id = None
            self.query_one("#playlist-table", TrackTable).set_tracks([])
        hdr = self.query_one("#playlists-header", Label)
        hdr.update(f"☰ Playlists   {len(self.playlists.items)}")
        self.query_one("#playlists-hint", Label).update(
            "N nueva · e renombrar · x borrar playlist · D descargar en MP3 · "
            "Guarda canciones desde cualquier lista con g"
            if self.playlists.items else
            "Aún no tienes playlists. Pulsa N para crear una, o pega un enlace de una playlist "
            "de YouTube/Spotify arriba y pulsa G.")
        self._update_sidebar_stats()

    @on(OptionList.OptionHighlighted, "#playlist-list")
    def _playlist_highlight(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self._show_playlist(event.option.id)

    @on(OptionList.OptionSelected, "#playlist-list")
    def _playlist_select(self, event: OptionList.OptionSelected) -> None:
        tbl = self.query_one("#playlist-table", TrackTable)
        if tbl.tracks:
            tbl.focus()

    def _show_playlist(self, pid: str) -> None:
        self._playlist_id = pid
        p = self.playlists.get(pid)
        tbl = self.query_one("#playlist-table", TrackTable)
        tbl.playing_key = self.queue.current.key if self.queue.current else None
        tbl.set_tracks(p.tracks if p else [])

    def action_new_playlist(self) -> None:
        def named(name: str | None) -> None:
            if name:
                p = self.playlists.create(name)
                self._refresh_playlists()
                ol = self.query_one("#playlist-list", OptionList)
                ol.highlighted = len(self.playlists.items) - 1
                self.notify(f"Playlist «{p.name}» creada. Añade canciones con g desde cualquier lista.")
        self.push_screen(TextPrompt("Nueva playlist", "Nombre"), named)

    def action_rename(self) -> None:
        view = self.query_one("#views", ContentSwitcher).current
        if view == "playlists" and self._playlist_id:
            p = self.playlists.get(self._playlist_id)
            if not p:
                return

            def done(name: str | None) -> None:
                if name:
                    self.playlists.rename(p.id, name)
                    self._refresh_playlists(keep=True)
            self.push_screen(TextPrompt("Renombrar playlist", value=p.name), done)
        elif view == "library" and self._library_folder != ALL_FOLDERS:
            f = self.library.folder(self._library_folder)
            if not f:
                return

            def done2(name: str | None) -> None:
                if name:
                    self.library.rename_folder(f.id, name)
                    self._refresh_folders(keep=True)
            self.push_screen(TextPrompt("Renombrar biblioteca", value=f.name), done2)

    def on_key(self, event) -> None:  # noqa: ANN001
        # 'x' sobre las listas laterales borra la playlist / biblioteca seleccionada
        if event.key not in ("x", "delete"):
            return
        focused = self.focused
        if focused is self.query_one("#playlist-list") and self._playlist_id:
            p = self.playlists.get(self._playlist_id)
            if p:
                event.stop()

                def ok(yes: bool) -> None:
                    if yes:
                        self.playlists.delete(p.id)
                        self._refresh_playlists()
                        self.notify(f"Playlist «{p.name}» eliminada")
                self.push_screen(Confirm(f"¿Borrar la playlist «{p.name}»?",
                                         "Los archivos descargados no se borran.", "Borrar"), ok)
        elif focused is self.query_one("#folders") and self._library_folder != ALL_FOLDERS:
            f = self.library.folder(self._library_folder)
            if f:
                event.stop()

                def ok2(yes: bool) -> None:
                    if yes:
                        self.library.remove_folder(f.id)
                        self._library_folder = ALL_FOLDERS
                        self._refresh_folders()
                        self._refresh_library_table()
                self.push_screen(Confirm(f"¿Quitar la biblioteca «{f.name}»?",
                                         f"Solo se deja de mostrar; no se borra nada de {f.path}",
                                         "Quitar"), ok2)

    # ================================================================ biblioteca
    def _refresh_folders(self, keep: bool = False) -> None:
        ol = self.query_one("#folders", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        ol.add_option(Option(_two_lines("▤ Todas las canciones", f"{self.library.count()} canciones"),
                             id=ALL_FOLDERS))
        for f in self.library.folders:
            path = f.path if len(f.path) <= 24 else "…" + f.path[-23:]
            ol.add_option(Option(_two_lines(f"▸ {f.name}", f"{self.library.count(f.id)} · {path}"),
                                 id=f.id))
        ol.highlighted = prev if keep and prev is not None and prev < ol.option_count else 0

    @on(OptionList.OptionHighlighted, "#folders")
    def _folder_highlight(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self._library_folder:
            self._library_folder = event.option.id
            self._refresh_library_table()

    @on(OptionList.OptionSelected, "#folders")
    def _folder_select(self, event: OptionList.OptionSelected) -> None:
        self.query_one("#library-table").focus()

    @on(Input.Changed, "#library-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._apply_library_filter()

    @on(Input.Submitted, "#library-filter")
    def _filter_submit(self, event: Input.Submitted) -> None:
        self.query_one("#library-table").focus()

    def _refresh_library_table(self) -> None:
        fid = None if self._library_folder == ALL_FOLDERS else self._library_folder
        self._library_tracks = self.library.tracks(fid)
        self._apply_library_filter()

    def _apply_library_filter(self) -> None:
        q = fold(self.query_one("#library-filter", Input).value.strip())
        tracks = self._library_tracks
        if q:
            words = q.split()
            tracks = [t for t in tracks
                      if all(w in fold(f"{t.title} {t.artist} {t.album}") for w in words)]
        tbl = self.query_one("#library-table", TrackTable)
        tbl.playing_key = self.queue.current.key if self.queue.current else None
        tbl.set_tracks(tracks, keep_cursor=False)
        total = len(self._library_tracks)
        name = "Todas las canciones"
        if self._library_folder != ALL_FOLDERS and (f := self.library.folder(self._library_folder)):
            name = f.name
        shown = f"{len(tracks)} de {total}" if q else f"{total}"
        self.query_one("#library-header", Label).update(f"▤ Biblioteca · {name}   {shown} canciones")
        self.query_one("#library-hint", Label).update(
            "Enter reproducir · a añadir a la cola · g guardar en playlist · "
            "f añadir carpeta · R re-escanear · e renombrar"
            if total or self.library.folders else
            "Pulsa f para añadir una carpeta con música (o pega/arrastra su ruta en la barra de arriba).")

    def action_add_folder(self) -> None:
        def chosen(path: str | None) -> None:
            if path:
                self._add_folder(path)
        self.push_screen(TextPrompt(
            "Añadir carpeta a la biblioteca", "/ruta/a/tu/música",
            hint="Consejo: puedes arrastrar la carpeta a la terminal para pegar su ruta."), chosen)

    def _add_folder(self, path: str) -> None:
        p = Path(path.strip().strip("'\"")).expanduser()
        if not p.is_dir():
            self.notify(f"No existe la carpeta {p}", severity="error")
            return
        f = self.library.add_folder(str(p))
        self.notify(f"Biblioteca «{f.name}» añadida. Escaneando…")
        self._library_folder = f.id
        self._refresh_folders()
        self.action_view("library")
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
        self._refresh_folders(keep=True)
        self._refresh_library_table()
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
            # Una playlist importada que se descarga se guarda también como playlist,
            # y sus canciones pasan a apuntar a los MP3 cuando terminan.
            col = self._results
            existing = next((p for p in self.playlists.items
                             if col.url and p.source_url == col.url), None)
            if existing is None:
                existing = self.playlists.create(col.name, tracks, source_url=col.url)
                self._refresh_playlists(keep=True)
                self.notify(f"Guardada también como playlist «{existing.name}»", timeout=4)
            group = existing.name
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
            if self._playlist_id:
                self._show_playlist(self._playlist_id)
        if job.finished:
            self._check_group_done(job.group)
            if self.query_one("#views", ContentSwitcher).current == "library":
                self._refresh_library_table()
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
                msg += f", {bad} con error (pulsa 5 para verlas)"
            self.notify(msg, title="Descarga terminada", severity="warning" if bad else "information",
                        timeout=8)
            self._refresh_folders(keep=True)
            self._refresh_playlists(keep=True)
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
            f"Se guardan en {self.cfg.download_dir} · MP3 "
            f"{'V0 (~245 kbps)' if self.cfg.mp3_quality == '0' else self.cfg.mp3_quality + ' kbps'}"
            " con portada y etiquetas · d sobre cualquier canción para descargarla")
