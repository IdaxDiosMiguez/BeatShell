"""La aplicación por encima de la ventana: reproductor, temas, icono de bandeja y segundo plano.

La ventana es desechable. En segundo plano se destruye entera (no solo se oculta) y
quedan únicamente el reproductor y un icono en la barra de menús / bandeja del sistema;
al volver se crea de nuevo, en el mismo sitio, con la cola y los resultados de antes.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPixmapCache
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from BeatShell.config import DATA_DIR

from . import __version__, icons, memory, style, workers
from .player import Player
from .settings import DesktopSettings

SERVER_NAME = "beatshell-desktop-" + hashlib.sha1(str(DATA_DIR).encode()).hexdigest()[:10]


class App(QApplication):
    """Sabe distinguir «salir» (⌘Q, menú, Salir) de «cerrar la ventana»."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.quitting = False

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.Quit:
            self.quitting = True
        return super().event(e)


def app_icon() -> QIcon:
    here = Path(__file__).resolve().parent
    base = Path(getattr(sys, "_MEIPASS", here.parent))
    for cand in (base / "beatshell_desktop" / "assets" / "icon.png", here / "assets" / "icon.png"):
        if cand.exists():
            return QIcon(str(cand))
    return QIcon()


def tray_icon() -> QIcon:
    """En macOS, plantilla monocroma (se adapta a la barra de menús); en el resto, el logo."""
    if sys.platform != "darwin":
        return app_icon()
    px = QPixmap(44, 44)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("black"))
    p.drawRoundedRect(QRectF(8, 8, 13, 13), 2.5, 2.5)
    p.drawRoundedRect(QRectF(23, 23, 13, 13), 2.5, 2.5)
    p.end()
    px.setDevicePixelRatio(2.0)
    ic = QIcon(px)
    ic.setIsMask(True)
    return ic


class Shell(QObject):
    def __init__(self, app: App, initial: str | None) -> None:
        super().__init__()
        self.app = app
        self.settings = DesktopSettings.load()
        self.theme_key = os.environ.get("BEATSHELL_THEME") or self.settings.theme
        self.theme = style.current(self.theme_key)
        self.player = Player()
        self.win = None
        self.results = None           # últimos resultados de búsqueda (sobreviven a la ventana)
        self.background = False
        self._bg_since = 0.0
        self._initial = initial
        self.tray: QSystemTrayIcon | None = None
        self._tray_now = None

        app.setQuitOnLastWindowClosed(False)
        app.setPalette(style.palette(self.theme))
        app.setStyleSheet(style.qss(self.theme))
        app.aboutToQuit.connect(self._cleanup)
        try:
            app.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except AttributeError:
            pass
        app.applicationStateChanged.connect(self._app_state)
        self.player.now_changed.connect(lambda _t: self._update_tray())
        self.player.state_changed.connect(self._update_tray)

        self._server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)
        self._server.listen(SERVER_NAME)
        self._server.newConnection.connect(self._other_instance)

    # ------------------------------------------------------------ arranque
    def start(self, background: bool = False) -> None:
        if background and QSystemTrayIcon.isSystemTrayAvailable():
            QTimer.singleShot(0, self.go_background)  # sin ventana desde el principio
        else:
            self.show_window()
        QTimer.singleShot(0, self._boot)

    def _boot(self) -> None:
        self.player.boot()
        if self._initial and self.win is None:
            # en segundo plano desde el arranque: como un bot, suena el primer resultado
            self._play_initial_headless(self._initial)
        elif self._initial and self.win is not None:
            from BeatShell.sources import classify

            text = self._initial
            if classify(text)[0] == "search" and not os.path.exists(text):
                text = "+" + text
            self.win.submit(text)

    def _play_initial_headless(self, text: str) -> None:
        from BeatShell.sources import classify

        kind, value = classify(text)
        pl = self.player

        def run():  # noqa: ANN202
            from BeatShell.sources import youtube

            if kind == "search":
                found = youtube.search(value, 1)
                return found[:1]
            if kind == "youtube_video":
                return [youtube.video_track(value)]
            if kind == "youtube_playlist":
                return youtube.playlist(value).tracks
            return []

        workers.run(run, done=lambda tracks: tracks and pl.enqueue(tracks))

    @staticmethod
    def forward_to_running(argv_text: str | None, background: bool = False) -> bool:
        """Si ya hay un BeatShell abierto, le pide que se muestre (o que pase a segundo plano)."""
        sock = QLocalSocket()
        sock.connectToServer(SERVER_NAME)
        if not sock.waitForConnected(300):
            return False
        cmd = background if isinstance(background, str) else ("background" if background else "show")
        sock.write((cmd + "\n" + (argv_text or "")).encode("utf-8"))
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True

    def _other_instance(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return

        def read() -> None:
            data = bytes(sock.readAll()).decode("utf-8", "replace")
            cmd, _, text = data.partition("\n")
            if cmd == "background":
                self.go_background()
                return
            if cmd == "quit":
                self.quit()
                return
            self.show_window()
            if text.strip() and self.win is not None:
                self.win.submit(text.strip())

        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)

    # ------------------------------------------------------------ ventana
    def show_window(self) -> None:
        from .app import MainWindow

        if self.win is None:
            self.win = MainWindow(self)
            if self.settings.geometry:
                self.win.restoreGeometry(QByteArray.fromBase64(self.settings.geometry.encode()))
        if self.background:
            self.background = False
            self.player.set_low_power(False)
            if self.tray is not None:
                self.tray.hide()
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def _save_geometry(self) -> None:
        if self.win is not None:
            self.settings.geometry = bytes(self.win.saveGeometry().toBase64()).decode()
            self.settings.save()

    def go_background(self) -> None:
        """Cierra la ventana del todo; la música sigue con el mínimo de recursos."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # Sin bandeja (algunos escritorios Linux) no habría forma de volver: minimizamos.
            if self.win is not None:
                self.win.showMinimized()
            self.player.set_low_power(True)
            return
        self._ensure_tray()
        self.tray.show()
        self._save_geometry()
        if self.win is not None:
            win, self.win = self.win, None
            win.teardown()
            win.hide()
            win.deleteLater()
        self.background = True
        self._bg_since = time.monotonic()
        self.player.set_low_power(True)
        self._update_tray()
        if not self.settings.background_tip_shown:
            self.settings.background_tip_shown = True
            self.settings.save()
            where = "la barra de menús" if sys.platform == "darwin" else "la bandeja del sistema"
            self.tray.showMessage("BeatShell sigue sonando",
                                  f"Vuelve desde su icono en {where}.", app_icon(), 4000)
        # Cuando la ventana ya no existe, devolvemos al sistema la memoria que ocupaba.
        QTimer.singleShot(400, self._give_back_memory)
        QTimer.singleShot(3000, self._give_back_memory)

    def _give_back_memory(self) -> None:
        if not self.background:
            return
        from BeatShell import art

        with art._mem_lock:
            art._mem_cache.clear()
        icons.pixmap.cache_clear()
        QPixmapCache.clear()
        lib = self.player.library
        lib._cache, lib._cache_sig = [], None
        memory.give_back()

    def close_requested(self) -> bool:
        """La ventana pregunta qué hacer al cerrarse. True = salir de la app."""
        if self.app.quitting:
            return True
        playing = self.player.playing
        if (self.settings.close_action == "background" and playing
                and QSystemTrayIcon.isSystemTrayAvailable()):
            QTimer.singleShot(0, self.go_background)
            return False
        QTimer.singleShot(0, self.quit)
        return False

    def quit(self) -> None:
        self.app.quitting = True
        self.app.quit()

    def _cleanup(self) -> None:
        self._save_geometry()
        if self.win is not None:
            self.win.teardown()
        if self.tray is not None:
            self.tray.hide()
        self.player.shutdown()
        workers.shutdown()
        self.settings.save()

    def _app_state(self, state) -> None:  # noqa: ANN001
        # macOS: pulsar el icono del Dock (o reabrir la app) con la ventana cerrada la recupera.
        if (sys.platform == "darwin" and self.background and state == Qt.ApplicationState.ApplicationActive
                and time.monotonic() - self._bg_since > 1.5):
            self.show_window()

    # ------------------------------------------------------------ bandeja
    def _ensure_tray(self) -> None:
        if self.tray is not None:
            return
        self.tray = QSystemTrayIcon(tray_icon(), self)
        menu = QMenu()
        self._tray_now = menu.addAction("")
        self._tray_now.setEnabled(False)
        menu.addSeparator()
        self._tray_play = menu.addAction("Pausa", self.player.toggle_pause)
        menu.addAction("Siguiente", self.player.next)
        menu.addAction("Anterior", self.player.prev)
        menu.addSeparator()
        menu.addAction("Abrir BeatShell", self.show_window)
        menu.addAction("Salir", self.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)

    def _tray_clicked(self, reason) -> None:  # noqa: ANN001
        if sys.platform != "darwin" and reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                   QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _update_tray(self) -> None:
        if self.tray is None or self._tray_now is None:
            return
        cur = self.player.queue.current
        text = f"{cur.title} · {cur.display_artist}" if cur else "Nada sonando"
        self._tray_now.setText(text if len(text) < 60 else text[:57] + "…")
        self._tray_play.setText("Pausa" if self.player.playing else "Reproducir")
        self.tray.setToolTip(f"BeatShell — {text}")

    # ------------------------------------------------------------ temas
    def set_theme(self, key: str) -> None:
        self.theme_key = key
        self.settings.theme = key
        self.settings.save()
        style.assign(self.theme, style.current(key))
        self.app.setPalette(style.palette(self.theme))
        self.app.setStyleSheet(style.qss(self.theme))
        if self.win is not None:
            self.win.retheme()

    def tint(self, theme: "style.Theme | None") -> None:
        """Tiñe toda la interfaz con otro tema sin guardarlo (None: vuelve al tema elegido).

        Lo usa la pantalla de inicio para ponerse de los colores de la portada."""
        style.assign(self.theme, theme if theme is not None else style.current(self.theme_key))
        self.app.setPalette(style.palette(self.theme))
        self.app.setStyleSheet(style.qss(self.theme))
        if self.win is not None:
            self.win.retheme()

    def _system_scheme_changed(self, *_a) -> None:  # noqa: ANN002
        if self.theme_key == "auto":
            self.set_theme("auto")

    # ------------------------------------------------------------ utilidades
    def toast(self, text: str, error: bool = False) -> None:
        if self.win is not None:
            self.win.toast.show_message(text, error)

    def memory_parts(self) -> list[tuple[str, float | None]]:
        pl = self.player
        audio = getattr(getattr(pl.engine, "_proc", None), "pid", None)
        yt = pl.extractor._proc.pid if pl.extractor is not None and pl.extractor.alive else None
        return [("interfaz", memory.rss_mb()),
                ("audio", memory.rss_mb(audio) if audio else None),
                ("YouTube", memory.rss_mb(yt) if yt else None)]


def run(argv: list[str]) -> int:
    App.setApplicationName("BeatShell")
    App.setApplicationDisplayName("BeatShell")
    App.setApplicationVersion(__version__)
    App.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = App(sys.argv[:1])
    if any(a in ("--salir", "--quit") for a in argv):
        return 0 if Shell.forward_to_running(None, "quit") else 1
    background = any(a in ("--segundo-plano", "--background") for a in argv)
    initial = " ".join(a for a in argv if not a.startswith(("-psn_", "--"))).strip() or None
    if not os.environ.get("BEATSHELL_SELFTEST") and Shell.forward_to_running(initial, background):
        return 0  # ya había uno abierto: se lo hemos pasado
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":
        f = QFont("Segoe UI")
        f.setPointSizeF(9.5)
        app.setFont(f)
    workers.init()
    shell = Shell(app, initial)
    shell.start(background=background)
    if os.environ.get("BEATSHELL_SELFTEST"):
        _selftest(shell, os.environ["BEATSHELL_SELFTEST"])
    return app.exec()


def _selftest(shell: Shell, out: str) -> None:
    """Comprobación automática (CI): arranca, espera y escribe el estado del motor en `out`."""
    import json

    def report() -> None:
        pl = shell.player
        eng = pl.engine
        data = {
            "booted": pl.booted,
            "engine_error": pl.engine_error,
            "audio_ready": bool(eng and eng.ready),
            "active": bool(eng and eng.active),
            "position": eng.position if eng else 0.0,
            "now": pl.queue.current.title if pl.queue.current else None,
        }
        Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        shell.quit()

    QTimer.singleShot(int(float(os.environ.get("BEATSHELL_SELFTEST_SECONDS", "8")) * 1000), report)
