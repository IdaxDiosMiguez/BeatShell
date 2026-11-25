"""Página de Ajustes: apariencia, segundo plano, reproducción, descargas y Spotify."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from BeatShell import eq
from BeatShell.config import DATA_DIR

from . import __version__, style, workers
from .widgets import Switch, ThemeCard

if TYPE_CHECKING:
    from .shell import Shell


class SettingsPage(QScrollArea):
    def __init__(self, shell: "Shell") -> None:
        super().__init__(objectName="settings")
        self.shell = shell
        self.t = shell.theme
        self.cfg = shell.player.cfg
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="settingsBody")
        self.setWidget(body)
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(28, 18, 28, 32)
        self.v.setSpacing(8)
        title = QLabel("Ajustes", objectName="pageTitle")
        self.v.addWidget(title)

        self._appearance()
        self._background()
        self._playback()
        self._downloads()
        self._spotify()
        self._about()
        self.v.addStretch(1)

        self._mem_timer = QTimer(self, interval=2500)
        self._mem_timer.timeout.connect(self.refresh_memory)

    # ------------------------------------------------------------ piezas
    def _section(self, name: str, hint: str = "") -> QVBoxLayout:
        self.v.addWidget(QLabel(name, objectName="setSection"))
        if hint:
            h = QLabel(hint, objectName="setHint")
            h.setWordWrap(True)
            self.v.addWidget(h)
        card = QFrame(objectName="setCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(0)
        self.v.addWidget(card)
        return lay

    def _row(self, lay: QVBoxLayout, label: str, widget: QWidget, hint: str = "") -> None:
        if lay.count():
            sep = QFrame(objectName="setRowSep")
            lay.addWidget(sep)
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 10)
        row.setSpacing(16)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.addWidget(QLabel(label, objectName="setLabel"))
        if hint:
            h = QLabel(hint, objectName="setHint")
            h.setWordWrap(True)
            txt.addWidget(h)
        row.addLayout(txt, 1)
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        w = QWidget()
        w.setLayout(row)
        lay.addWidget(w)

    def _switch(self, on: bool, changed: Callable[[bool], None]) -> Switch:
        s = Switch(self.t)
        s.setChecked(on)
        s.toggled.connect(changed)
        return s

    def _combo(self, items: list[tuple[str, str]], current: str,
               changed: Callable[[str], None], width: int = 240) -> QComboBox:
        c = QComboBox()
        c.setFixedWidth(width)
        for key, label in items:
            c.addItem(label, key)
        idx = c.findData(current)
        c.setCurrentIndex(max(0, idx))
        c.currentIndexChanged.connect(lambda _i: changed(c.currentData()))
        return c

    def _save_cfg(self) -> None:
        try:
            self.cfg.save()
        except OSError as exc:
            self.shell.toast(f"No se pudo guardar: {exc}", True)

    # ------------------------------------------------------------ secciones
    def _appearance(self) -> None:
        lay = self._section("Apariencia", "Los mismos temas que BeatShell en la terminal. "
                                          "«Automático» sigue el modo claro u oscuro del sistema.")
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 12, 0, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.cards: list[ThemeCard] = []
        for i, (key, (name, th)) in enumerate(style.THEMES.items()):
            card = ThemeCard(key, name, th, self.t)
            card.setChecked(key == self.shell.theme_key)
            card.clicked.connect(lambda _=False, k=key: self._pick_theme(k))
            grid.addWidget(card, i // 4, i % 4)
            self.cards.append(card)
        grid.setColumnStretch(4, 1)
        lay.addWidget(grid_w)
        self._row(lay, "Animaciones", self._switch(self.shell.settings.animations, self._set_anim),
                  "Las barritas que bailan junto a la canción que suena.")

    def _pick_theme(self, key: str) -> None:
        self.shell.set_theme(key)
        for c in self.cards:
            c.setChecked(c.key == key)

    def _set_anim(self, on: bool) -> None:
        self.shell.settings.animations = on
        self.shell.settings.save()
        if self.shell.win is not None:
            self.shell.win._mark_playing()

    def _background(self) -> None:
        lay = self._section(
            "Segundo plano",
            "Para jugar o trabajar con música de fondo: la ventana se cierra del todo, la música "
            "sigue y BeatShell se queda en la barra de menús (bandeja del sistema en Windows y Linux) "
            "usando el mínimo de memoria y procesador. Para volver, pulsa su icono.")
        go = QPushButton("Pasar a segundo plano")
        go.setObjectName("primary")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self.shell.go_background)
        self._row(lay, "Ahora", go, "Atajo: Ctrl+B (⌘B en Mac).")
        self._row(lay, "Al cerrar la ventana con música sonando",
                  self._combo([("background", "Seguir sonando en segundo plano"),
                               ("quit", "Salir de BeatShell")],
                              self.shell.settings.close_action, self._set_close, 300))
        self.mem_label = QLabel("…", objectName="setHint")
        self.mem_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mem_label.setMinimumWidth(260)
        self._row(lay, "Memoria en uso ahora", self.mem_label,
                  "La interfaz, el audio y el lector de YouTube (que se apaga solo cuando descansa).")

    def _set_close(self, value: str) -> None:
        self.shell.settings.close_action = value
        self.shell.settings.save()

    def refresh_memory(self) -> None:
        def show(parts: list[tuple[str, float | None]]) -> None:
            total = sum(v for _k, v in parts if v)
            detail = " · ".join(f"{k} {v or 0:.0f}" for k, v in parts)
            self.mem_label.setText(f"{total:.0f} MB   ({detail})")

        workers.run(self.shell.memory_parts, done=show)  # medir lanza `ps`: fuera del hilo de la UI

    def _playback(self) -> None:
        pl = self.shell.player
        lay = self._section("Reproducción")
        self._row(lay, "Ecualizador",
                  self._combo([(n, n) for n in eq.PRESETS], self.cfg.eq_preset, pl.set_eq, 200),
                  "También en el botón ≋ del reproductor.")
        self.dj_switch = self._switch(self.cfg.dj_mode, lambda _on: pl.toggle_dj())
        self._row(lay, "Modo DJ", self.dj_switch,
                  "Fundido entre canciones, volumen nivelado y radio infinita al acabar la cola.")
        fade = QSpinBox()
        fade.setRange(1, 20)
        fade.setSuffix(" s")
        fade.setFixedWidth(90)
        fade.setValue(int(round(self.cfg.dj_crossfade)))
        fade.valueChanged.connect(self._set_fade)
        self._row(lay, "Duración del fundido", fade, "Segundos que se solapan dos canciones en modo DJ.")

    def _set_fade(self, v: int) -> None:
        self.cfg.dj_crossfade = float(v)
        self._save_cfg()

    def _downloads(self) -> None:
        lay = self._section("Descargas")
        self.dir_btn = QPushButton(self._short(self.cfg.download_dir))
        self.dir_btn.setToolTip(self.cfg.download_dir)
        self.dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dir_btn.clicked.connect(self._pick_dir)
        self._row(lay, "Carpeta", self.dir_btn, "Cada playlist o álbum va en su propia subcarpeta.")
        self._row(lay, "Calidad del MP3",
                  self._combo([("0", "Variable V0 · ~245 kbps (recomendada)"), ("320", "Constante · 320 kbps")],
                              self.cfg.mp3_quality, self._set_quality, 300))
        conc = QSpinBox()
        conc.setRange(1, 6)
        conc.setFixedWidth(90)
        conc.setValue(int(self.cfg.concurrent_downloads))
        conc.valueChanged.connect(self._set_conc)
        self._row(lay, "Descargas a la vez", conc, "Se aplica la próxima vez que abras BeatShell.")

    @staticmethod
    def _short(path: str) -> str:
        p = str(Path(path).expanduser())
        home = str(Path.home())
        p = "~" + p[len(home):] if p.startswith(home) else p
        return p if len(p) <= 34 else "…" + p[-33:]

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Carpeta de descargas",
                                                str(Path(self.cfg.download_dir).expanduser()))
        if not path:
            return
        self.cfg.download_dir = path
        self._save_cfg()
        self.dir_btn.setText(self._short(path))
        self.dir_btn.setToolTip(path)
        self.shell.player._ensure_download_folder()
        self.shell.player.library_changed.emit()

    def _set_quality(self, v: str) -> None:
        self.cfg.mp3_quality = v
        self._save_cfg()

    def _set_conc(self, v: int) -> None:
        self.cfg.concurrent_downloads = int(v)
        self._save_cfg()

    def _spotify(self) -> None:
        lay = self._section(
            "Spotify (opcional)",
            "Sin nada aquí ya puedes abrir canciones, álbumes y playlists de Spotify. Solo hace falta "
            "para playlists de más de 100 canciones: crea una app gratis en developer.spotify.com/dashboard.")
        cid = QLineEdit(self.cfg.spotify_client_id, objectName="setField")
        cid.setPlaceholderText("Client ID")
        cid.setFixedWidth(280)
        cid.editingFinished.connect(lambda: self._set_spotify("spotify_client_id", cid.text()))
        sec = QLineEdit(self.cfg.spotify_client_secret, objectName="setField")
        sec.setPlaceholderText("Client Secret")
        sec.setEchoMode(QLineEdit.EchoMode.Password)
        sec.setFixedWidth(280)
        sec.editingFinished.connect(lambda: self._set_spotify("spotify_client_secret", sec.text()))
        self._row(lay, "Client ID", cid)
        self._row(lay, "Client Secret", sec)

    def _set_spotify(self, key: str, value: str) -> None:
        if getattr(self.cfg, key) != value.strip():
            setattr(self.cfg, key, value.strip())
            self._save_cfg()

    def _about(self) -> None:
        lay = self._section("BeatShell")
        data = QPushButton("Abrir carpeta de datos")
        data.setCursor(Qt.CursorShape.PointingHandCursor)
        data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
        self._row(lay, f"Versión {__version__}", data,
                  "Biblioteca, playlists, cola y ajustes, compartidos con la versión de terminal.")
        rescan = QPushButton("Volver a leer")
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.clicked.connect(self.shell.player.rescan)
        self._row(lay, "Biblioteca", rescan, "Busca canciones nuevas en tus carpetas de música.")

    # ------------------------------------------------------------ visibilidad
    def showEvent(self, e) -> None:  # noqa: ANN001, N802
        super().showEvent(e)
        self.dj_switch.blockSignals(True)
        self.dj_switch.setChecked(self.cfg.dj_mode)
        self.dj_switch.blockSignals(False)
        self.refresh_memory()
        self._mem_timer.start()

    def hideEvent(self, e) -> None:  # noqa: ANN001, N802
        self._mem_timer.stop()
        super().hideEvent(e)
