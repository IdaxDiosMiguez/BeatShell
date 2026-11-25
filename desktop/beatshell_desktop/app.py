"""Ventana principal de BeatShell Desktop."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from BeatShell import eq
from BeatShell.library import (
    ALBUM,
    ARTIST,
    AUDIO_EXTS,
    FOLDER,
    PLAYLIST,
    export_m3u,
    import_m3u,
    track_from_file,
)
from BeatShell.models import Track, fmt_time
from BeatShell.sources import Collection, classify

from . import icons, workers
from .settings_page import SettingsPage
from .widgets import Bar, Cover, DownloadDelegate, DownloadModel, PlayButton, Toast, TrackList

if TYPE_CHECKING:
    from .shell import Shell

SEARCH, QUEUE, COLLECTION, DOWNLOADS, SETTINGS = range(5)
KIND_ICON = {PLAYLIST: "list", ALBUM: "album", ARTIST: "artist"}


def _human_duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min"


def _summary(tracks: list[Track]) -> str:
    n = len(tracks)
    total = sum(t.duration or 0 for t in tracks)
    s = f"{n} canci{'ón' if n == 1 else 'ones'}"
    return s + (f" · {_human_duration(total)}" if total > 60 else "")


# ====================================================================== página
class Page(QWidget):
    """Cabecera (título, subtítulo, botones) + contenido."""

    def __init__(self, title: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 18, 20, 0)
        lay.setSpacing(4)
        self.title = QLabel(title, objectName="pageTitle")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sub = QLabel("", objectName="pageSub")
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 12, 0, 8)
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        lay.addLayout(self.actions)
        self.body = lay

    def button(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            b.setObjectName("primary")
        self.actions.insertWidget(self.actions.count() - 1, b)
        return b


# ====================================================================== ventana
class MainWindow(QMainWindow):
    def __init__(self, shell: "Shell") -> None:
        super().__init__()
        self.shell = shell
        self.t = shell.theme
        self.player = shell.player
        self._results: Collection | None = None
        self._conns: list = []         # conexiones al reproductor (se sueltan al cerrar)
        self._icons: list = []         # iconos a repintar al cambiar de tema
        self._search_seq = 0
        self._coll_sel: tuple[str, str | None] = ("all", None)
        self._coll_tracks: list[Track] = []
        self.setWindowTitle("BeatShell")
        self.resize(1180, 760)
        self.setMinimumSize(820, 540)
        self.setAcceptDrops(True)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        right = QWidget(objectName="content")
        v = QVBoxLayout(right)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_queue_page())
        self.stack.addWidget(self._build_collection_page())
        self.stack.addWidget(self._build_downloads_page())
        self.settings_page = SettingsPage(shell)
        self.stack.addWidget(self.settings_page)
        v.addWidget(self.stack, 1)
        h.addWidget(right, 1)

        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        ov.addWidget(root, 1)
        ov.addWidget(self._build_player_bar())
        self.setCentralWidget(outer)
        self.toast = Toast(outer)

        self._wire()
        self._shortcuts()
        self._refresh_collections()
        self._refresh_queue()
        self._on_now(self.player.queue.current)
        self._on_state()
        if self.player.downloader is not None:
            self.d_model.reset(self.player.downloader.jobs)
            self._update_downloads_label()
        if shell.results is not None:
            self._show_results(shell.results, False, self._search_seq)
            self.show_page(QUEUE)
        else:
            self.show_page(QUEUE if self.player.queue.tracks else SEARCH)

    # ------------------------------------------------------------ construcción
    def _build_sidebar(self) -> QWidget:
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 18, 12, 12)
        v.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.setSpacing(6)
        dot = QLabel("▚", objectName="brandDot")
        brand.addWidget(dot)
        brand.addWidget(QLabel("BeatShell", objectName="brand"))
        brand.addStretch(1)
        v.addLayout(brand)

        self.nav: dict[int, QPushButton] = {}
        for page, (label, ic) in {SEARCH: ("Buscar", "search"), QUEUE: ("Cola", "queue"),
                                  DOWNLOADS: ("Descargas", "download")}.items():
            b = QPushButton(label, objectName="nav")
            b.setCheckable(True)
            self._icon(b, ic, 20)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=page: self.show_page(p))
            v.addWidget(b)
            self.nav[page] = b

        head = QHBoxLayout()
        head.setContentsMargins(0, 18, 0, 4)
        head.addWidget(QLabel("TU BIBLIOTECA", objectName="sectionTitle"))
        head.addStretch(1)
        add = QToolButton()
        self._icon(add, "plus", 16)
        add.setToolTip("Nueva playlist, añadir carpeta o importar")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        menu.addAction("Nueva playlist", self._new_playlist)
        menu.addAction("Añadir carpeta de música…", self._pick_folder)
        menu.addAction("Importar playlist (.m3u8)…", self._import_playlist)
        menu.addSeparator()
        menu.addAction("Volver a leer la biblioteca", self.player.rescan)
        add.setMenu(menu)
        head.addWidget(add)
        v.addLayout(head)

        self.collections = QListWidget(objectName="collections")
        self.collections.setIconSize(QSize(16, 16))
        self.collections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.collections.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.collections.customContextMenuRequested.connect(self._collection_menu)
        self.collections.itemClicked.connect(self._collection_clicked)
        self.collections.itemDoubleClicked.connect(lambda _i: self._play_collection())
        v.addWidget(self.collections, 1)

        b = QPushButton("Ajustes", objectName="nav")
        b.setCheckable(True)
        self._icon(b, "settings", 20)
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False: self.show_page(SETTINGS))
        v.addWidget(b)
        self.nav[SETTINGS] = b
        return side

    def _build_header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(28, 16, 20, 2)
        self.search = QLineEdit(objectName="search")
        self.search.setPlaceholderText("Busca en YouTube o pega un enlace de YouTube / Spotify")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(620)
        self.search.setMinimumHeight(40)
        lead = QAction("", self.search)
        self._icon(lead, "search", 18, normal="faint", active="")
        self.search.addAction(lead, QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(self._submit_search)
        self.search.textEdited.connect(lambda _t: self.player.warm_youtube())
        h.addWidget(self.search, 1)
        h.addStretch(0)
        return w

    def _build_search_page(self) -> QWidget:
        p = Page("Buscar")
        p.sub.setText("Escribe arriba lo que quieras escuchar · también enlaces de YouTube y Spotify")
        self.s_play = p.button("Reproducir todo", primary=True)
        self.s_queue = p.button("Añadir a la cola")
        self.s_save = p.button("Guardar en biblioteca")
        self.s_dl = p.button("Descargar MP3")
        for b in (self.s_play, self.s_queue, self.s_save, self.s_dl):
            b.hide()
        self.s_list = TrackList(self.t, empty="Tus resultados aparecerán aquí")
        p.body.addWidget(self.s_list, 1)
        self.p_search = p
        return p

    def _build_queue_page(self) -> QWidget:
        p = Page("Cola")
        self.q_clear = p.button("Vaciar cola")
        self.q_list = TrackList(self.t, empty="La cola está vacía.\nBusca algo o abre tu biblioteca.")
        p.body.addWidget(self.q_list, 1)
        self.p_queue = p
        return p

    def _build_collection_page(self) -> QWidget:
        p = Page("Biblioteca")
        self.c_play = p.button("Reproducir", primary=True)
        self.c_shuffle = p.button("Aleatorio")
        self.c_dl = p.button("Descargar MP3")
        self.c_filter = QLineEdit(objectName="filter")
        self.c_filter.setPlaceholderText("Filtrar…")
        self.c_filter.setFixedWidth(220)
        p.actions.addWidget(self.c_filter)
        self.c_list = TrackList(self.t, empty="")
        p.body.addWidget(self.c_list, 1)
        self.p_coll = p
        return p

    def _build_downloads_page(self) -> QWidget:
        p = Page("Descargas")
        self.d_retry = p.button("Reintentar fallidas")
        self.d_clear = p.button("Limpiar terminadas")
        self.d_open = p.button("Abrir carpeta")
        self.d_model = DownloadModel()
        self.d_list = QListView()
        self.d_list.setModel(self.d_model)
        self.d_list.setItemDelegate(DownloadDelegate(self.t, self.d_list))
        self.d_list.setUniformItemSizes(True)
        self.d_list.setMouseTracking(True)
        self.d_list.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.d_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        p.body.addWidget(self.d_list, 1)
        self.p_dl = p
        return p

    def _tool(self, name: str, tip: str, size: int = 20) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setIconSize(QSize(size, size))
        self._icon(b, name, size)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _icon(self, target, name: str, size: int = 20, normal: str = "muted", active: str = "text") -> None:  # noqa: ANN001
        """Pone un icono y lo apunta para repintarlo con los colores del tema nuevo."""
        def apply() -> None:
            target.setIcon(icons.icon(name, getattr(self.t, normal), getattr(self.t, active) if active else None,
                                      size))
        apply()
        self._icons.append(apply)

    def _build_player_bar(self) -> QWidget:
        bar = QFrame(objectName="player")
        bar.setFixedHeight(96)
        g = QGridLayout(bar)
        g.setContentsMargins(16, 12, 20, 12)
        g.setHorizontalSpacing(12)

        # izquierda: portada + título
        left = QHBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        self.cover = Cover(64, 10)
        left.addWidget(self.cover)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.addStretch(1)
        self.now_title = QLabel("", objectName="nowTitle")
        self.now_artist = QLabel("", objectName="nowArtist")
        for lab in (self.now_title, self.now_artist):
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        txt.addWidget(self.now_title)
        txt.addWidget(self.now_artist)
        txt.addStretch(1)
        left.addLayout(txt, 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMinimumWidth(180)
        g.addWidget(lw, 0, 0)

        # centro: controles + progreso
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        self.b_shuffle = self._tool("shuffle", "Aleatorio (Ctrl+S)", 18)
        self.b_shuffle.setCheckable(True)
        self.b_prev = self._tool("prev", "Anterior (Ctrl+←)", 22)
        self.b_play = PlayButton(self.t, 40)
        self.b_play.setToolTip("Reproducir / pausa (Espacio)")
        self.b_next = self._tool("next", "Siguiente (Ctrl+→)", 22)
        self.b_repeat = self._tool("repeat", "Repetir (Ctrl+R)", 18)
        self.b_repeat.setCheckable(True)
        for b in (self.b_shuffle, self.b_prev, self.b_play, self.b_next, self.b_repeat):
            row.addWidget(b)
        row.addStretch(1)
        center.addLayout(row)
        prog = QHBoxLayout()
        prog.setSpacing(10)
        self.t_pos = QLabel("0:00", objectName="time")
        self.t_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.t_pos.setFixedWidth(44)
        self.t_dur = QLabel("0:00", objectName="time")
        self.t_dur.setFixedWidth(44)
        for lab in (self.t_pos, self.t_dur):
            f = lab.font()
            try:  # cifras del mismo ancho: el tiempo no baila mientras avanza
                f.setFeature(QFont.Tag("tnum"), 1)
            except (AttributeError, TypeError):
                pass
            lab.setFont(f)
        self.seek = Bar(self.t, gradient=True)
        self.seek.hover_changed.connect(self._seek_hover)
        self._seek_hover_frac: float | None = None
        prog.addWidget(self.t_pos)
        prog.addWidget(self.seek, 1)
        prog.addWidget(self.t_dur)
        center.addLayout(prog)
        cw = QWidget()
        cw.setLayout(center)
        cw.setMaximumWidth(640)
        g.addWidget(cw, 0, 1)

        # derecha: EQ y volumen
        right = QHBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)
        right.addStretch(1)
        self.b_eq = self._tool("eq", "Ecualizador y modo DJ", 18)
        self.b_eq.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.eq_menu = QMenu(self.b_eq)
        self.b_eq.setMenu(self.eq_menu)
        self.eq_actions: dict[str, QAction] = {}
        for name in eq.PRESETS:
            a = self.eq_menu.addAction(name)
            a.setCheckable(True)
            a.setToolTip(eq.PRESET_HINT.get(name, ""))
            a.triggered.connect(lambda _=False, n=name: self.player.set_eq(n))
            a.hovered.connect(lambda n=name: self.player.set_eq(n, save=False))
            self.eq_actions[name] = a
        self.eq_menu.aboutToHide.connect(lambda: self.player.set_eq(self.player.cfg.eq_preset, save=False))
        self.eq_menu.addSeparator()
        self.dj_action = self.eq_menu.addAction("Modo DJ (fundidos y radio infinita)")
        self.dj_action.setCheckable(True)
        self.dj_action.triggered.connect(self.player.toggle_dj)
        self.b_vol = self._tool("volume", "Silenciar (M)", 18)
        self.vol = Bar(self.t, live=True, gradient=False, wheel_step=0.05)
        self.vol.setFixedWidth(112)
        self.b_bg = self._tool("moon", "Segundo plano: cierra la ventana y sigue sonando "
                                       "gastando lo mínimo (Ctrl+B)", 18)
        self.b_bg.clicked.connect(self.shell.go_background)
        right.addWidget(self.b_bg)
        right.addWidget(self.b_eq)
        right.addWidget(self.b_vol)
        right.addWidget(self.vol)
        rw = QWidget()
        rw.setLayout(right)
        rw.setMinimumWidth(180)
        g.addWidget(rw, 0, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 2)
        g.setColumnStretch(2, 1)
        return bar

    # ------------------------------------------------------------ conexiones
    def _wire(self) -> None:
        pl = self.player
        for sig, slot in ((pl.now_changed, self._on_now), (pl.state_changed, self._on_state),
                          (pl.queue_changed, self._refresh_queue), (pl.position, self._on_position),
                          (pl.toast, self.toast.show_message), (pl.job_changed, self._on_job),
                          (pl.library_changed, self._on_library_changed), (pl.scanning, self._on_scanning)):
            sig.connect(slot)
            self._conns.append((sig, slot))

        self.b_play.clicked.connect(pl.toggle_pause)
        self.b_next.clicked.connect(pl.next)
        self.b_prev.clicked.connect(pl.prev)
        self.b_shuffle.clicked.connect(pl.toggle_shuffle)
        self.b_repeat.clicked.connect(pl.cycle_repeat)
        self.b_vol.clicked.connect(pl.toggle_mute)
        self.vol.seek.connect(lambda f: pl.set_volume(int(round(f * 100))))
        self.seek.seek.connect(self._seek_fraction)

        # buscar
        self.s_list.play_row.connect(self._play_search_row)
        self.s_list.menu.connect(lambda rows, pos: self._track_menu(self.s_list, rows, pos))
        self.s_play.clicked.connect(lambda: self._results and pl.play_list(self._results.tracks, 0))
        self.s_queue.clicked.connect(lambda: self._results and pl.enqueue(self._results.tracks))
        self.s_save.clicked.connect(self._save_results)
        self.s_dl.clicked.connect(lambda: self._results and pl.download(
            self._results.tracks, None if self._results.kind == "search" else self._results.name))
        # cola
        self.q_list.play_row.connect(pl.jump)
        self.q_list.delete_rows.connect(pl.remove_from_queue)
        self.q_list.menu.connect(lambda rows, pos: self._track_menu(self.q_list, rows, pos))
        self.q_clear.clicked.connect(pl.clear_queue)
        # biblioteca
        self.c_list.play_row.connect(lambda r: pl.play_list(self.c_list.tracks, r))
        self.c_list.menu.connect(lambda rows, pos: self._track_menu(self.c_list, rows, pos))
        self.c_list.delete_rows.connect(self._remove_from_playlist)
        self.c_play.clicked.connect(self._play_collection)
        self.c_shuffle.clicked.connect(self._shuffle_collection)
        self.c_dl.clicked.connect(self._download_collection)
        self._filter_timer = QTimer(self, singleShot=True, interval=120)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.c_filter.textChanged.connect(lambda _t: self._filter_timer.start())
        # descargas
        self.d_retry.clicked.connect(lambda: pl.downloader and pl.downloader.retry_failed())
        self.d_clear.clicked.connect(self._clear_downloads)
        self.d_open.clicked.connect(self._open_downloads)

    def _shortcuts(self) -> None:
        pl = self.player
        binds = {
            "Space": pl.toggle_pause,
            "Ctrl+Right": pl.next,
            "Ctrl+Left": pl.prev,
            "Shift+Right": lambda: pl.seek_relative(10),
            "Shift+Left": lambda: pl.seek_relative(-10),
            "Ctrl+Up": lambda: pl.set_volume(pl.volume + 5),
            "Ctrl+Down": lambda: pl.set_volume(pl.volume - 5),
            "M": pl.toggle_mute,
            "Ctrl+S": pl.toggle_shuffle,
            "Ctrl+R": pl.cycle_repeat,
            "Ctrl+F": self.focus_search,
            "Ctrl+L": self.focus_search,
            "Ctrl+K": self.focus_search,
            "/": self.focus_search,
            "Ctrl+1": lambda: self.show_page(SEARCH),
            "Ctrl+2": lambda: self.show_page(QUEUE),
            "Ctrl+3": lambda: self._select_collection(("all", None)),
            "Ctrl+4": lambda: self.show_page(DOWNLOADS),
            "Ctrl+O": self._pick_folder,
            "Ctrl+N": self._new_playlist,
            "Ctrl+D": pl.toggle_dj,
            "Ctrl+B": self.shell.go_background,
            "Ctrl+,": lambda: self.show_page(SETTINGS),
            "Ctrl+Q": self.shell.quit,
        }
        for seq, fn in binds.items():
            QShortcut(QKeySequence(seq), self, activated=fn)
        for key, fn in ((Qt.Key.Key_MediaPlay, pl.toggle_pause),
                        (Qt.Key.Key_MediaTogglePlayPause, pl.toggle_pause),
                        (Qt.Key.Key_MediaPause, pl.toggle_pause),
                        (Qt.Key.Key_MediaNext, pl.next),
                        (Qt.Key.Key_MediaPrevious, pl.prev)):
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence("Escape"), self.search, activated=self._leave_search)

    # ------------------------------------------------------------ navegación
    def show_page(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        for p, b in self.nav.items():
            b.setChecked(p == page)
        if page != COLLECTION:
            self.collections.clearSelection()
        if page == SEARCH and not self.s_list.tracks:
            self.focus_search()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _leave_search(self) -> None:
        self.search.clear()
        page = self.stack.currentWidget()
        lst = {self.p_search: self.s_list, self.p_queue: self.q_list, self.p_coll: self.c_list}.get(page)
        if lst is not None:
            lst.setFocus()

    # ------------------------------------------------------------ reproductor
    def _on_now(self, track: Track | None) -> None:
        self.cover.set_track(track)
        if track is None:
            self.now_title.setText("Nada sonando")
            self.now_artist.setText("Busca algo para empezar")
            self.setWindowTitle("BeatShell")
        else:
            self.now_title.setText(track.title)
            self.now_title.setToolTip(track.title)
            self.now_artist.setText(track.display_artist)
            self.setWindowTitle(f"{track.title} · {track.display_artist}")
        self._mark_playing()

    def _mark_playing(self) -> None:
        cur = self.player.queue.current
        anim = self.player.playing and self.shell.settings.animations
        key = cur.key if cur else None
        self.s_list.set_playing(key, animate=anim)
        self.c_list.set_playing(key, animate=anim)
        self.q_list.set_playing(key, cur.uid if cur else None, animate=anim)

    def _on_state(self) -> None:
        pl, t = self.player, self.t
        playing = pl.engine is not None and pl.engine.active and not pl.engine.paused
        loading = pl.loading is not None or (pl.engine is not None and pl.engine.active and pl.engine.buffering)
        self.b_play.set_icon_name("pause" if playing or pl.loading else "play")
        self.b_play.set_loading(loading)
        self.seek.set_loading(loading)
        self.b_play.setToolTip("Cargando…" if loading else "Reproducir / pausa (Espacio)")
        self.b_shuffle.setChecked(pl.queue.shuffle)
        self.b_shuffle.setIcon(icons.icon("shuffle", t.accent if pl.queue.shuffle else t.muted,
                                          t.accent if pl.queue.shuffle else t.text, 18))
        rep = pl.queue.repeat
        self.b_repeat.setChecked(rep != "off")
        self.b_repeat.setIcon(icons.icon("repeat-one" if rep == "one" else "repeat",
                                         t.accent if rep != "off" else t.muted,
                                         t.accent if rep != "off" else t.text, 18))
        self.b_repeat.setToolTip({"off": "Repetir: no", "all": "Repetir: toda la cola",
                                  "one": "Repetir: esta canción"}[rep] + " (Ctrl+R)")
        vol = 0 if pl.muted else pl.volume
        self.vol.set_fraction(vol / 100)
        self.vol.setToolTip(f"Volumen {vol} %  ·  rueda del ratón o Ctrl+↑↓")
        self.b_vol.setIcon(icons.icon("mute" if vol == 0 else "volume-low" if vol < 45 else "volume",
                                      t.muted, t.text, 18))
        for name, a in self.eq_actions.items():
            a.setChecked(name == pl.cfg.eq_preset)
        self.dj_action.setChecked(pl.cfg.dj_mode)
        active_eq = pl.cfg.eq_preset != eq.DEFAULT or pl.cfg.dj_mode
        self.b_eq.setIcon(icons.icon("eq", t.accent if active_eq else t.muted,
                                     t.accent if active_eq else t.text, 18))
        self.b_eq.setToolTip(f"Ecualizador: {pl.cfg.eq_preset}" + (" · Modo DJ" if pl.cfg.dj_mode else ""))
        self._mark_playing()

    def _on_position(self, pos: float, dur) -> None:  # noqa: ANN001
        if self.player.loading is not None:
            self.t_pos.setText("…")
            self.seek.set_fraction(0)
        elif self.seek.dragging:
            self._time_preview(dur, self.seek.fraction)
        else:
            self.seek.set_fraction(pos / dur if dur else 0)
            if self._seek_hover_frac is not None and dur:
                self._time_preview(dur, self._seek_hover_frac)
            else:
                self.t_pos.setStyleSheet("")
                self.t_pos.setText(fmt_time(pos))
        self.t_dur.setText(fmt_time(dur) if dur else "--:--")

    def _time_preview(self, dur, frac: float) -> None:  # noqa: ANN001
        """Mientras apuntas o arrastras, el reloj enseña adónde saltarías (en el acento)."""
        self.t_pos.setStyleSheet(f"color: {self.t.accent};")
        self.t_pos.setText(fmt_time((dur or 0) * frac))

    def _seek_hover(self, frac) -> None:  # noqa: ANN001
        self._seek_hover_frac = frac

    def _seek_fraction(self, frac: float) -> None:
        eng = self.player.engine
        if eng is not None and eng.active and eng.duration:
            self.player.seek(frac * eng.duration)

    def _refresh_queue(self) -> None:
        q = self.player.queue
        self.q_list.set_tracks(q.tracks, keep_scroll=True)
        self.p_queue.sub.setText(_summary(q.tracks) if q.tracks else "")
        self._mark_playing()

    # ------------------------------------------------------------ búsqueda
    def _submit_search(self) -> None:
        if self.submit(self.search.text().strip()):
            self.search.clear()

    def submit(self, text: str) -> bool:
        """Procesa la barra de arriba. Devuelve True si hay que vaciarla."""
        if not text:
            return False
        candidate = text.strip("'\"")
        if candidate.startswith("~") or os.path.isabs(candidate) or os.path.exists(candidate):
            if self._open_path(Path(candidate).expanduser()):
                return True
        quick = text.startswith("+")
        if quick:
            text = text[1:].strip()
        kind, value = classify(text)
        self._search_seq += 1
        seq = self._search_seq
        if kind == "search":
            self._loading_results(f"Buscando «{value}»…")

            def run() -> Collection:
                from BeatShell.sources import youtube

                return Collection(name=value, tracks=youtube.search(value, 25), kind="search")

            workers.run(run, done=lambda col: self._show_results(col, quick, seq),
                        fail=lambda e: self._search_failed(str(e), seq))
            return False
        if kind in ("youtube_playlist", "spotify"):
            single = kind == "spotify" and value.startswith("track:")
            if not single:
                self._loading_results("Leyendo la playlist…" if kind == "youtube_playlist" else "Leyendo Spotify…")
            cfg = self.player.cfg

            def run_col() -> Collection:
                if kind == "spotify":
                    from BeatShell.sources import spotify

                    col = spotify.fetch(value, cfg.spotify_client_id, cfg.spotify_client_secret)
                else:
                    from BeatShell.sources import youtube

                    col = youtube.playlist(value)
                if not col.tracks:
                    raise RuntimeError("No tiene canciones disponibles")
                return col

            def got(col: Collection) -> None:
                if single:
                    self.player.enqueue([col.tracks[0]])
                else:
                    self._show_results(col, False, seq)

            workers.run(run_col, done=got, fail=lambda e: self._search_failed(str(e), seq))
            return True
        self.toast.show_message("Abriendo enlace…")

        def run_video() -> Track:
            from BeatShell.sources import youtube

            return youtube.video_track(value)

        workers.run(run_video, done=lambda t: self.player.enqueue([t]),
                    fail=lambda e: self.toast.show_message(f"No se pudo abrir: {e}", True))
        return True

    def _loading_results(self, message: str) -> None:
        self.show_page(SEARCH)
        self.p_search.title.setText(message)
        self.p_search.sub.setText("")
        self.s_list.empty_text = ""
        self.s_list.set_tracks([])
        for b in (self.s_play, self.s_queue, self.s_save, self.s_dl):
            b.hide()

    def _search_failed(self, error: str, seq: int) -> None:
        if seq != self._search_seq:
            return
        self.p_search.title.setText("No se pudo abrir")
        self.p_search.sub.setText(error[:200])
        self.s_list.empty_text = "Revisa la conexión o prueba otra búsqueda"
        self.s_list.viewport().update()

    def _show_results(self, col: Collection, quick: bool, seq: int) -> None:
        if seq != self._search_seq:
            return
        self._results = col
        self.shell.results = col
        self.p_search.title.setText(col.name if col.kind != "search" else f"«{col.name}»")
        label = {"album": "Álbum", "playlist": "Playlist"}.get(col.kind, "Resultados de YouTube")
        owner = f" · {col.owner}" if col.owner else ""
        n = len(col.tracks)
        count = _summary(col.tracks) if col.kind != "search" else f"{n} resultado{'s' if n != 1 else ''}"
        self.p_search.sub.setText(f"{label}{owner} · {count}")
        self.s_list.empty_text = "Sin resultados"
        self.s_list.set_tracks(col.tracks)
        is_col = col.kind != "search"
        self.s_play.setVisible(is_col)
        self.s_queue.setVisible(is_col)
        self.s_save.setVisible(is_col)
        self.s_dl.setVisible(is_col)
        self._mark_playing()
        if quick and col.tracks:
            self.player.enqueue([col.tracks[0]])
        elif col.tracks and self.search.hasFocus():
            self.s_list.setFocus()
            self.s_list.setCurrentIndex(self.s_list.model().index(0, 0))

    def _play_search_row(self, row: int) -> None:
        col = self._results
        if col is None or row >= len(col.tracks):
            return
        if col.kind == "search":
            self.player.play_now(col.tracks[row])
        else:
            self.player.play_list(col.tracks, row)

    def _save_results(self) -> None:
        col = self._results
        if col is None:
            return
        kind = ALBUM if col.kind == "album" else PLAYLIST
        if self.player.playlists.find(kind, col.name, col.owner if kind == ALBUM else ""):
            self.toast.show_message("Ya está en tu biblioteca")
            return
        p = self.player.playlists.create(col.name, col.tracks, source_url=col.url, kind=kind,
                                         artist=col.owner if kind == ALBUM else "", thumb=col.thumb)
        self._refresh_collections()
        self.toast.show_message(f"«{p.name}» guardada en tu biblioteca")

    # ------------------------------------------------------------ rutas y arrastrar
    def _open_path(self, path: Path) -> bool:
        if path.is_dir():
            self.player.add_folder(str(path))
            return True
        if path.is_file() and path.suffix.lower() in (".m3u", ".m3u8"):
            self._import_playlist(str(path))
            return True
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
            self.player.enqueue([track_from_file(path)])
            return True
        return False

    def dragEnterEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: ANN001, N802
        md = e.mimeData()
        files = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
        if files:
            audio = [f for f in files if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
            if audio:
                self.player.enqueue([track_from_file(f) for f in sorted(audio)])
            for f in files:
                if f not in audio:
                    self._open_path(f)
            return
        text = md.text() or (md.urls()[0].toString() if md.urls() else "")
        if text:
            self.submit(text.strip())

    # ------------------------------------------------------------ biblioteca
    def _refresh_collections(self) -> None:
        lib, store, t = self.player.library, self.player.playlists, self.t
        sel = self._coll_sel if self.stack.currentIndex() == COLLECTION else None
        self.collections.blockSignals(True)
        self.collections.clear()

        def add(text: str, icon: str, data: tuple, tip: str = "") -> None:
            it = QListWidgetItem(icons.icon(icon, t.muted, t.text, 16), text)
            it.setData(Qt.ItemDataRole.UserRole, data)
            it.setToolTip(tip or text)
            self.collections.addItem(it)
            if data == sel:
                it.setSelected(True)

        add("Todas las canciones", "note", ("all", None), f"{lib.count()} canciones en tu equipo")
        for f in lib.folders:
            add(f.name, "folder", ("dir", f.id), f.path)
        order = {PLAYLIST: 0, ALBUM: 1, ARTIST: 2}
        items = sorted((p for p in store.items if p.kind != FOLDER),
                       key=lambda p: (order.get(p.kind, 9), p.name.lower()))
        for p in items:
            sub = f"{p.artist} · " if p.artist else ""
            add(p.name, KIND_ICON.get(p.kind, "list"), ("pl", p.id), f"{sub}{len(p.tracks)} canciones")
        self.collections.blockSignals(False)

    def _collection_clicked(self, item: QListWidgetItem) -> None:
        self._select_collection(item.data(Qt.ItemDataRole.UserRole))

    def _select_collection(self, sel: tuple) -> None:
        if sel != self._coll_sel:
            self.c_filter.blockSignals(True)
            self.c_filter.clear()
            self.c_filter.blockSignals(False)
        self._coll_sel = tuple(sel)
        self.stack.setCurrentIndex(COLLECTION)
        for b in self.nav.values():
            b.setChecked(False)
        for i in range(self.collections.count()):
            it = self.collections.item(i)
            it.setSelected(tuple(it.data(Qt.ItemDataRole.UserRole)) == self._coll_sel)
        self._load_collection(keep_scroll=False)

    def _load_collection(self, keep_scroll: bool = True) -> None:
        kind, ident = self._coll_sel
        lib, store = self.player.library, self.player.playlists
        p = self.p_coll
        is_pl = kind == "pl"
        if kind == "all":
            tracks, title = lib.tracks(), "Todas las canciones"
            empty = ("Aún no hay música en tu equipo.\nArrastra aquí una carpeta o pulsa + en "
                     "«Tu biblioteca».")
        elif kind == "dir":
            f = lib.folder(ident)
            if f is None:
                return self._select_collection(("all", None))
            tracks, title, empty = lib.tracks(ident), f.name, "Esta carpeta no tiene canciones"
        else:
            pl = store.get(ident)
            if pl is None:
                return self._select_collection(("all", None))
            tracks, title = list(pl.tracks), pl.name
            empty = "Playlist vacía.\nAñade canciones con clic derecho → Añadir a playlist."
        self._coll_tracks = tracks
        p.title.setText(title)
        p.sub.setText(_summary(tracks) if tracks else "")
        self.c_dl.setVisible(is_pl)
        self.c_list.model_.numbered = is_pl
        self.c_list.empty_text = empty
        self._apply_filter(keep_scroll)

    def _apply_filter(self, keep_scroll: bool = False) -> None:
        q = self.c_filter.text().strip().lower()
        tracks = self._coll_tracks
        if q:
            words = q.split()
            tracks = [t for t in tracks
                      if all(w in f"{t.title} {t.artist} {t.album}".lower() for w in words)]
        self.c_list.set_tracks(tracks, keep_scroll=keep_scroll)
        self._mark_playing()

    def _on_library_changed(self) -> None:
        self._refresh_collections()
        if self.stack.currentIndex() == COLLECTION:
            self._load_collection()

    def _on_scanning(self, on: bool) -> None:
        if on and self.stack.currentIndex() == COLLECTION and self._coll_sel[0] != "pl":
            self.p_coll.sub.setText("Leyendo tu música…")

    def _play_collection(self) -> None:
        if self.c_list.tracks:
            self.player.play_list(self.c_list.tracks, 0)

    def _shuffle_collection(self) -> None:
        if not self.c_list.tracks:
            return
        import random

        if not self.player.queue.shuffle:
            self.player.toggle_shuffle()
        self.player.play_list(self.c_list.tracks, random.randrange(len(self.c_list.tracks)))

    def _download_collection(self) -> None:
        if self._coll_sel[0] == "pl" and (pl := self.player.playlists.get(self._coll_sel[1])):
            self.player.download(pl.tracks, pl.name)

    def _remove_from_playlist(self, rows: list[int]) -> None:
        if self._coll_sel[0] != "pl":
            return
        pid = self._coll_sel[1]
        for t in [self.c_list.tracks[r] for r in rows]:
            self.player.playlists.remove(pid, t.uid)
        self._load_collection()
        self._refresh_collections()

    def _collection_menu(self, pos) -> None:  # noqa: ANN001
        item = self.collections.itemAt(pos)
        if item is None:
            return
        kind, ident = item.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Reproducir", lambda: (self._select_collection((kind, ident)), self._play_collection()))
        if kind == "dir":
            f = self.player.library.folder(ident)
            m.addAction("Mostrar en el explorador",
                        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(f.path)) if f else None)
            m.addAction("Volver a leer", self.player.rescan)
            m.addSeparator()
            m.addAction("Quitar de la biblioteca", lambda: self._remove_folder(ident))
        elif kind == "pl":
            m.addAction("Renombrar…", lambda: self._rename_playlist(ident))
            m.addAction("Exportar .m3u8…", lambda: self._export_playlist(ident))
            m.addSeparator()
            m.addAction("Eliminar", lambda: self._delete_playlist(ident))
        else:
            m.addAction("Volver a leer la biblioteca", self.player.rescan)
        m.exec(self.collections.viewport().mapToGlobal(pos))

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Añadir carpeta de música", str(Path.home()))
        if path:
            self.player.add_folder(path)

    def _remove_folder(self, ident: str) -> None:
        f = self.player.library.folder(ident)
        if f and self._confirm(f"¿Quitar «{f.name}» de la biblioteca?",
                               "Los archivos no se borran de tu equipo."):
            self.player.library.remove_folder(ident)
            if self._coll_sel == ("dir", ident):
                self._coll_sel = ("all", None)
            self._on_library_changed()

    def _new_playlist(self, tracks: list[Track] | None = None) -> None:
        name, ok = QInputDialog.getText(self, "Nueva playlist", "Nombre:")
        if not ok:
            return
        p = self.player.playlists.create(name, tracks or [])
        self._refresh_collections()
        if tracks:
            self.toast.show_message(f"{len(tracks)} añadida(s) a «{p.name}»")
        else:
            self._select_collection(("pl", p.id))

    def _rename_playlist(self, pid: str) -> None:
        p = self.player.playlists.get(pid)
        if p is None:
            return
        name, ok = QInputDialog.getText(self, "Renombrar", "Nombre:", text=p.name)
        if ok and name.strip():
            self.player.playlists.rename(pid, name)
            self._refresh_collections()
            if self._coll_sel == ("pl", pid):
                self.p_coll.title.setText(name.strip())

    def _delete_playlist(self, pid: str) -> None:
        p = self.player.playlists.get(pid)
        if p and self._confirm(f"¿Eliminar «{p.name}»?", "Las canciones descargadas no se borran."):
            self.player.playlists.delete(pid)
            if self._coll_sel == ("pl", pid):
                self._select_collection(("all", None))
            self._refresh_collections()

    def _export_playlist(self, pid: str) -> None:
        p = self.player.playlists.get(pid)
        if p is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar playlist",
                                              str(Path.home() / f"{p.name}.m3u8"), "Playlist (*.m3u8)")
        if path:
            try:
                out = export_m3u(p, path)
                self.toast.show_message(f"Exportada a {out.name}")
            except OSError as exc:
                self.toast.show_message(f"No se pudo exportar: {exc}", True)

    def _import_playlist(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Importar playlist", str(Path.home()),
                                                  "Playlists (*.m3u *.m3u8)")
        if not path:
            return
        try:
            name, tracks, missing = import_m3u(path)
        except (OSError, ValueError) as exc:
            self.toast.show_message(f"No se pudo importar: {exc}", True)
            return
        p = self.player.playlists.create(name, tracks)
        self._refresh_collections()
        self._select_collection(("pl", p.id))
        extra = f" ({missing} no encontradas)" if missing else ""
        self.toast.show_message(f"«{p.name}» importada: {len(tracks)} canciones{extra}", bool(missing))

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("BeatShell")
        box.setText(title)
        box.setInformativeText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    # ------------------------------------------------------------ menú de canciones
    def _track_menu(self, lst: TrackList, rows: list[int], pos) -> None:  # noqa: ANN001
        tracks = [lst.tracks[r] for r in rows if r < len(lst.tracks)]
        if not tracks:
            return
        pl = self.player
        m = QMenu(self)
        if lst is self.q_list:
            m.addAction("Reproducir", lambda: pl.jump(rows[0]))
        else:
            m.addAction("Reproducir", lambda: pl.play_list(tracks, 0) if len(tracks) > 1
                        else (self._play_search_row(rows[0]) if lst is self.s_list
                              else pl.play_list(lst.tracks, rows[0])))
        m.addAction("Reproducir a continuación", lambda: pl.enqueue(tracks, next_=True))
        if lst is not self.q_list:
            m.addAction("Añadir a la cola", lambda: pl.enqueue(tracks))
        m.addSeparator()
        sub = m.addMenu("Añadir a playlist")
        sub.addAction("Nueva playlist…", lambda: self._new_playlist(tracks))
        lists = sorted(pl.playlists.playlists, key=lambda p: p.name.lower())
        if lists:
            sub.addSeparator()
        for p in lists:
            sub.addAction(p.name, lambda pid=p.id, name=p.name: (
                pl.playlists.add(pid, tracks), self._refresh_collections(),
                self.toast.show_message(f"Añadida(s) a «{name}»")))
        remote = [t for t in tracks if not (t.path and os.path.exists(t.path))]
        if remote:
            m.addAction("Descargar MP3", lambda: pl.download(remote))
        local = next((t for t in tracks if t.path and os.path.exists(t.path)), None)
        if local is not None:
            m.addAction("Mostrar en el explorador", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(local.path).parent))))
        if lst is self.q_list:
            m.addSeparator()
            m.addAction("Quitar de la cola", lambda: pl.remove_from_queue(rows))
        elif lst is self.c_list and self._coll_sel[0] == "pl":
            m.addSeparator()
            m.addAction("Quitar de la playlist", lambda: self._remove_from_playlist(rows))
        m.exec(pos)

    # ------------------------------------------------------------ descargas
    def _on_job(self, job) -> None:  # noqa: ANN001
        self.d_model.upsert(job)
        self._update_downloads_label()

    def _update_downloads_label(self) -> None:
        jobs = self.player.downloader.jobs if self.player.downloader else []
        active = sum(1 for j in jobs if not j.finished)
        self.nav[DOWNLOADS].setText(f"Descargas  ·  {active}" if active else "Descargas")
        failed = sum(1 for j in jobs if j.status == "error")
        parts = []
        if active:
            parts.append(f"{active} en curso")
        if failed:
            parts.append(f"{failed} con error")
        self.p_dl.sub.setText(" · ".join(parts) if parts else
                              f"Tus MP3 se guardan en {self.player.cfg.download_dir}")

    def _clear_downloads(self) -> None:
        if self.player.downloader:
            self.player.downloader.clear_finished()
            self.d_model.reset(self.player.downloader.jobs)
            self._update_downloads_label()

    def _open_downloads(self) -> None:
        path = Path(self.player.cfg.download_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------ ventana
    def resizeEvent(self, e) -> None:  # noqa: ANN001, N802
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast._place()

    def closeEvent(self, e) -> None:  # noqa: ANN001, N802
        if self.shell.close_requested():
            e.accept()
        else:
            e.ignore()

    def teardown(self) -> None:
        """Suelta las conexiones con el reproductor antes de destruir la ventana."""
        for sig, slot in self._conns:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._conns.clear()
        for lst in (self.s_list, self.q_list, self.c_list):
            lst.set_playing(None)

    def retheme(self) -> None:
        """El tema ya cambió (en el sitio): repintamos iconos y todo lo demás."""
        for apply in self._icons:
            apply()
        self._refresh_collections()
        self._on_state()
        for card in self.settings_page.cards:
            card.setChecked(card.key == self.shell.theme_key)
            card.update()
        for w in self.findChildren(QWidget):
            w.update()
