"""El cerebro de la app: cola, motor de audio, resolución de pistas y descargas.

Es la misma lógica que la versión de terminal (arranque instantáneo con cambio a alta
calidad en marcha, transiciones sin huecos, modo DJ con radio infinita), sin interfaz:
la ventana solo escucha sus señales.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from BeatShell import eq
from BeatShell.config import Config
from BeatShell.library import Library, MatchCache, PlaylistStore, load_session, save_session
from BeatShell.models import Track
from BeatShell.playqueue import PlayQueue

from . import workers


@dataclass
class PlayContext:
    """De dónde viene lo que suena: lo que enseña la pantalla de inicio."""

    kind: str                       # «Playlist», «Álbum», «Búsqueda»…
    name: str
    owner: str = ""
    thumb: str | None = None
    tracks: list = field(default_factory=list)  # para el mosaico si no hay portada
    sel: tuple | None = None         # para volver a esa lista desde el inicio


class Player(QObject):
    now_changed = Signal(object)        # Track | None
    state_changed = Signal()            # pausa, carga, aleatorio, repetir, volumen, EQ…
    queue_changed = Signal()
    position = Signal(float, object)    # (segundos, duración | None)
    toast = Signal(str, bool)           # (mensaje, es_error)
    job_changed = Signal(object)        # DownloadJob
    library_changed = Signal()
    scanning = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()
        if not eq.valid(self.cfg.eq_preset):
            self.cfg.eq_preset = eq.DEFAULT
        # De dónde se puso a sonar cada canción (playlist, álbum…), por canción: lo enseña
        # la pantalla de inicio. Pocas entradas (se reemplaza al poner otra lista).
        self._contexts: dict[str, PlayContext] = {}
        self.library = Library()
        self.playlists = PlaylistStore()
        self.matches = MatchCache()
        self.queue = PlayQueue()
        self.queue.repeat = self.cfg.repeat
        self.queue.shuffle = self.cfg.shuffle
        tracks, idx = load_session()
        if tracks:
            self.queue.tracks = tracks
            self.queue.index = min(idx, len(tracks) - 1) if idx >= 0 else -1

        # Lo pesado (motor de audio, yt-dlp) se carga después de enseñar la ventana.
        self.engine = None
        self.engine_error: str | None = None
        self.resolver = None
        self.downloader = None
        self.youtube = None
        self.extractor = None
        self.low_power = False
        self.booted = False
        self._pending: tuple[Track | None, float] | None = None

        self.loading: Track | None = None
        self._load_token = 0
        self._fail_streak = 0
        self._muted_volume: int | None = None
        self._hq: tuple[int, object] | None = None
        self._hq_tries = 0
        self._next_info: tuple[int, str, int] | None = None
        self._next_src = None
        self._xfade_for = -1
        self._radio_for: str | None = None
        self._groups: dict[str, list[str]] = {}
        self._saved_version = -1
        self._saved_at = 0.0
        self._queue_seen = -1
        self._last_state: tuple = ()
        self._scanning = False

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    # ================================================================ arranque
    def boot(self) -> None:
        self._timer.start()
        workers.run(self._load_heavy, done=self._heavy_ready, fail=self._heavy_failed)
        self._ensure_download_folder()
        self.rescan()

    def _load_heavy(self):  # noqa: ANN202 - en un hilo
        from .extractor import Extractor, install, lazy_import

        lazy_import("yt_dlp")  # aquí solo hace falta para descargar; leer YouTube va aparte
        lazy_import("sounddevice")  # el sonido sale del proceso de audio, no de este
        from BeatShell.audio_process import AudioEngine
        from BeatShell.services import Downloader, Resolver
        from BeatShell.sources import youtube

        self.extractor = Extractor(idle_timeout=15 if self.low_power else 90)
        install(youtube, self.extractor)
        self.extractor.start()  # ya despierto para la primera búsqueda

        engine = None
        error = None
        try:
            engine = AudioEngine(volume=self.cfg.volume)
            eq.apply(engine, self.cfg.eq_preset, self.cfg.dj_mode, self.cfg.eq_custom)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        resolver = Resolver(self.matches, self.library)
        downloader = Downloader(self.cfg, resolver, self.library,
                                lambda job: workers.on_main(lambda: self._job_changed(job)))
        return engine, error, resolver, downloader, youtube

    def _heavy_ready(self, parts) -> None:  # noqa: ANN001
        self.engine, self.engine_error, self.resolver, self.downloader, self.youtube = parts
        self.booted = True
        if self.engine is not None:
            workers.run(self.engine.wait_ready, fail=lambda e: self._audio_failed(str(e)))
        else:
            self._audio_failed(self.engine_error or "Audio no disponible")
        if self._pending is not None:
            track, xfade = self._pending
            self._pending = None
            self._start(track, xfade)
        self.state_changed.emit()

    def _heavy_failed(self, exc: Exception) -> None:
        self.booted = True
        self._audio_failed(f"No se pudo iniciar BeatShell: {exc}")

    def _audio_failed(self, error: str) -> None:
        self.engine_error = error
        self.engine = None
        self.toast.emit(f"Sin audio: {error}", True)
        self.state_changed.emit()

    def _ensure_download_folder(self) -> None:
        dl = Path(self.cfg.download_dir).expanduser()
        try:
            dl.mkdir(parents=True, exist_ok=True)
            target = os.path.normcase(str(dl.resolve()))
            if not any(os.path.normcase(f.path) == target for f in self.library.folders):
                self.library.add_folder(str(dl), "Descargas BeatShell")
        except OSError:
            pass

    def shutdown(self) -> None:
        self._timer.stop()
        self.save_session()
        self.cfg.volume = self._muted_volume if self._muted_volume is not None else self.volume
        try:
            self.cfg.save()
        except OSError:
            pass
        if self.downloader is not None:
            self.downloader.shutdown()
        if self.extractor is not None:
            self.extractor.shutdown()
        if self.engine is not None:
            self.engine.close()

    def warm_youtube(self) -> None:
        """Despierta el lector de YouTube (p. ej. en cuanto empiezas a escribir una búsqueda)."""
        ex = self.extractor
        if ex is not None and not ex.alive:
            workers.run(ex.start)

    def set_low_power(self, on: bool) -> None:
        """Segundo plano: menos despertares y el lector de YouTube se apaga en cuanto descansa."""
        self.low_power = on
        self._timer.setInterval(250 if on else 50)
        if self.extractor is not None:
            self.extractor.idle_timeout = 15 if on else 90
            if on:
                self.extractor.stop()

    # ================================================================ estado
    @property
    def volume(self) -> int:
        return self.engine.volume if self.engine is not None else self.cfg.volume

    @property
    def playing(self) -> bool:
        eng = self.engine
        return bool(eng and eng.active and not eng.paused) or self.loading is not None

    @property
    def muted(self) -> bool:
        return self._muted_volume is not None

    def _tick(self) -> None:
        eng = self.engine
        if eng is not None:
            eng.pump()
            if eng.poll_advanced():
                self._on_advanced()
            if eng.poll_finished():
                self._on_track_end()
            if eng.active:
                self.position.emit(eng.position, eng.duration)
            elif self.loading is None:
                cur = self.queue.current
                self.position.emit(0.0, cur.duration if cur else None)
            if self.cfg.dj_mode:
                self._dj_tick(eng)
            else:
                self._gapless_tick(eng)
            if self._hq is not None and eng.is_fast and eng.upgrade_state != "pending":
                self._try_upgrade()
            state = (eng.active, eng.paused, eng.buffering, self.loading is not None)
            if state != self._last_state:
                self._last_state = state
                self.state_changed.emit()
        if self.queue.version != self._queue_seen:
            self._queue_seen = self.queue.version
            self.queue_changed.emit()
        if self.queue.version != self._saved_version and time.monotonic() - self._saved_at > 5:
            self.save_session()

    def save_session(self) -> None:
        self._saved_version = self.queue.version
        self._saved_at = time.monotonic()
        try:
            save_session(self.queue.tracks, self.queue.index)
        except OSError:
            pass

    # ================================================================ reproducir
    def _start(self, track: Track | None, xfade: float = 0.0) -> None:
        self._load_token += 1
        token = self._load_token
        if not self.booted:
            self._pending = (track, xfade)
            self.loading = track
            self.now_changed.emit(track)
            self.state_changed.emit()
            return
        if self.engine is None:
            self.toast.emit(self.engine_error or "Audio no disponible", True)
            return
        self._next_info = self._next_src = None
        if not (xfade > 0 and self.engine.active and not self.engine.paused):
            xfade = 0.0
            self.engine.stop()
        if track is None:
            self.loading = None
            self.now_changed.emit(None)
            self.state_changed.emit()
            return
        self.loading = track
        self.now_changed.emit(track)
        self.state_changed.emit()
        self.queue_changed.emit()
        had_thumb = track.thumb
        workers.run(self.resolver.resolve, track,
                    done=lambda src: self._loaded(track, token, src, had_thumb != track.thumb, xfade),
                    fail=lambda exc: self._load_failed(track, token, str(exc)))

    def _loaded(self, track: Track, token: int, src, thumb_changed: bool, xfade: float) -> None:  # noqa: ANN001
        if token != self._load_token or self.engine is None:
            return
        self.loading = None
        if xfade > 0 and self.engine.active:
            self.engine.crossfade_to(src, xfade)
        else:
            self.engine.play(src)
        self._hq, self._hq_tries = None, 0
        if src.fast:
            self._await_hq(track, token)
        if thumb_changed:
            self.now_changed.emit(track)
        self.state_changed.emit()
        self.resolver.prefetch(self.queue.peek_next())

    def _await_hq(self, track: Track, token: int) -> None:
        workers.run(self.resolver.hq_source, track,
                    done=lambda src: self._hq_ready(token, src) if src is not None else None)

    def _hq_ready(self, token: int, src) -> None:  # noqa: ANN001
        if token != self._load_token or self.engine is None:
            return
        self._hq = (token, src)
        self._try_upgrade()

    def _try_upgrade(self) -> None:
        eng = self.engine
        if eng is None or self._hq is None or self._hq[0] != self._load_token or not eng.is_fast:
            return
        if eng.crossfading or eng.buffering or self._hq_tries >= 4:
            return
        self._hq_tries += 1
        eng.upgrade(self._hq[1], lead=1.5 + self._hq_tries)

    def _load_failed(self, track: Track, token: int, error: str) -> None:
        if token != self._load_token:
            return
        self.loading = None
        self._fail_streak += 1
        self.toast.emit(f"No se pudo reproducir «{track.title}»: {error[:140]}", True)
        if self._fail_streak < 3 and self.queue.peek_next() is not None:
            QTimer.singleShot(800, self.next)
        else:
            self._fail_streak = 0
        self.state_changed.emit()

    def _on_track_end(self) -> None:
        if self.loading is not None:
            return
        eng = self.engine
        if eng and eng.error:
            self._load_failed(self.queue.current or Track(title="?"), self._load_token, eng.error)
            return
        self._fail_streak = 0
        nxt = self.queue.advance(auto=True)
        if nxt is None:
            self.state_changed.emit()
            return
        self._start(nxt)

    # ---------------------------------------------------------- sin huecos
    def _gapless_tick(self, eng) -> None:  # noqa: ANN001
        info = self._next_info
        if info is not None and info[0] == self._load_token and info[2] != self.queue.version:
            eng.clear_next()
            self._next_info = info = self._next_src = None
        cur = self.queue.current
        if cur is None or not eng.active or self.loading is not None:
            return
        dur = eng.duration
        if not dur or dur - eng.position > 10:
            return
        if info is not None and info[0] == self._load_token:
            return
        nxt = self.queue.peek_next()
        if nxt is None:
            return
        token = self._load_token
        self._next_info = (token, nxt.uid, self.queue.version)
        workers.run(self.resolver.resolve, nxt,
                    done=lambda src: self._next_ready(nxt, token, src))

    def _next_ready(self, track: Track, token: int, src) -> None:  # noqa: ANN001
        info = self._next_info
        if (self.engine is None or info is None or info[0] != token or token != self._load_token
                or info[1] != track.uid or self.cfg.dj_mode):
            return
        self._next_src = src
        self.engine.set_next(src)

    def _on_advanced(self) -> None:
        src = self._next_src
        self._next_info = self._next_src = None
        nxt = self.queue.advance(auto=True)
        self._load_token += 1
        self.loading = None
        self._fail_streak = 0
        self._hq, self._hq_tries = None, 0
        self.now_changed.emit(nxt)
        if nxt is not None and src is not None and src.fast:
            self._await_hq(nxt, self._load_token)
        self.resolver.prefetch(self.queue.peek_next())

    # ---------------------------------------------------------- modo DJ
    def _dj_tick(self, eng) -> None:  # noqa: ANN001
        cur = self.queue.current
        if (cur is None or not eng.active or eng.paused or eng.crossfading
                or self.loading is not None or eng.buffering):
            return
        dur, pos = eng.duration, eng.position
        if not dur or dur < 30:
            return
        remaining = dur - pos
        if remaining < 75 and self.queue.peek_next() is None and self._radio_for != cur.uid:
            self._radio_for = cur.uid
            workers.run(self._fetch_radio, cur, done=self._radio_ready)
        xf = min(float(self.cfg.dj_crossfade), dur / 4)
        if remaining <= xf and self._xfade_for != self._load_token:
            if self.queue.peek_next() is not None:
                self._xfade_for = self._load_token
                self._start(self.queue.advance(auto=True), xfade=max(1.0, remaining))

    def _fetch_radio(self, seed: Track) -> list[Track]:
        yt = self.youtube
        url = seed.url or self.resolver.ensure_url(seed)
        vid = yt.youtube_id(url) if url else None
        if not vid:
            found = yt.search(seed.search_query, 1)
            vid = yt.youtube_id(found[0].url) if found else None
        if not vid:
            return []
        return yt.playlist(f"https://www.youtube.com/watch?v={vid}&list=RD{vid}").tracks

    def _radio_ready(self, tracks: list[Track]) -> None:
        known = {t.key for t in self.queue.tracks}
        titles = {t.title.lower() for t in self.queue.tracks}
        new = [t for t in tracks if t.key not in known and t.title.lower() not in titles
               and t.duration and t.duration < 900][:10]
        if new:
            self.enqueue(new, quiet=True)
            self.toast.emit(f"Radio: {len(new)} canciones parecidas en la cola", False)

    # ================================================================ acciones
    def idle(self) -> bool:
        return (self.engine is None or not self.engine.active) and self.loading is None

    def play_list(self, tracks: list[Track], start: int = 0,
                  context: "PlayContext | None" = None) -> None:
        if tracks:
            self._contexts = {t.key: context for t in tracks} if context else {}
            self._start(self.queue.replace(tracks, start))

    def context_for(self, track: Track | None) -> "PlayContext | None":
        return self._contexts.get(track.key) if track is not None else None

    def play_now(self, track: Track, context: "PlayContext | None" = None) -> None:
        if context is not None:
            self._contexts[track.key] = context
        idx = self.queue.insert_next([track])
        self._start(self.queue.jump(idx))

    def jump(self, index: int) -> None:
        self._start(self.queue.jump(index))

    def enqueue(self, tracks: list[Track], next_: bool = False, quiet: bool = False) -> None:
        if not tracks:
            return
        idle = self.idle()
        if next_:
            self.queue.insert_next(tracks)
        else:
            self.queue.add(tracks)
        what = f"«{tracks[0].title}»" if len(tracks) == 1 else f"{len(tracks)} canciones"
        if idle:
            start = len(self.queue.tracks) - len(tracks) if not next_ else max(0, self.queue.index + 1)
            self._start(self.queue.jump(start))
        else:
            if not quiet:
                self.toast.emit(f"{what} {'a continuación' if next_ else 'en la cola'}", False)
            if self.resolver is not None:
                self.resolver.prefetch(self.queue.peek_next())

    def toggle_pause(self) -> None:
        if self.engine is None and self.booted:
            self.toast.emit(self.engine_error or "Audio no disponible", True)
            return
        if self.idle():
            cur = self.queue.current or self.queue.advance()
            if cur:
                self._start(cur)
            return
        if self.engine is not None and self.engine.active:
            self.engine.toggle_pause()
        self.state_changed.emit()

    def next(self) -> None:
        nxt = self.queue.advance()
        if nxt is not None:
            self._start(nxt, xfade=2.5 if self.cfg.dj_mode else 0.0)
        else:
            self.toast.emit("No hay más canciones en la cola", False)

    def prev(self) -> None:
        if self.engine and self.engine.active and self.engine.position > 4:
            self.seek(0)
            return
        prev = self.queue.back()
        if prev is not None:
            self._start(prev)

    def seek(self, seconds: float) -> None:
        eng = self.engine
        if eng is None or not eng.active:
            return
        if eng.is_fast and self._hq is not None and self._hq[0] == self._load_token:
            dur = eng.duration
            target = max(0.0, min(seconds, dur - 0.5) if dur else seconds)
            eng.play(self._hq[1], start=target)
            return
        eng.seek(seconds)

    def seek_relative(self, delta: float) -> None:
        if self.engine and self.engine.active:
            self.seek(self.engine.position + delta)

    def set_volume(self, value: int) -> None:
        self._muted_volume = None
        value = max(0, min(100, int(value)))
        if self.engine is not None:
            self.engine.volume = value
        self.cfg.volume = value
        self.state_changed.emit()

    def toggle_mute(self) -> None:
        if self._muted_volume is None:
            self._muted_volume = self.volume or 70
            if self.engine is not None:
                self.engine.volume = 0
        else:
            vol, self._muted_volume = self._muted_volume, None
            if self.engine is not None:
                self.engine.volume = vol
        self.state_changed.emit()

    def toggle_shuffle(self) -> None:
        self.queue.shuffle = not self.queue.shuffle
        self.cfg.shuffle = self.queue.shuffle
        self.cfg.save()
        if self.resolver is not None:
            self.resolver.prefetch(self.queue.peek_next())
        self.state_changed.emit()

    def cycle_repeat(self) -> None:
        self.cfg.repeat = self.queue.cycle_repeat()
        self.cfg.save()
        self.state_changed.emit()

    def set_eq(self, preset: str, save: bool = True) -> None:
        if save:
            self.cfg.eq_preset = preset
            self.cfg.save()
        if self.engine is not None:
            eq.apply(self.engine, preset, self.cfg.dj_mode, self.cfg.eq_custom)
        self.state_changed.emit()

    def set_custom_eq(self, gains, save: bool = False) -> None:  # noqa: ANN001
        """Ecualizador personalizado en directo: se oye en ~10 ms (más la salida de audio).

        Mientras se arrastra no se toca el disco; `save=True` al soltar."""
        self.cfg.eq_custom = list(eq.custom_gains(gains))
        changed = self.cfg.eq_preset != eq.CUSTOM
        self.cfg.eq_preset = eq.CUSTOM
        if self.engine is not None:
            self.engine.set_eq(self.cfg.eq_custom)
        if save:
            self.cfg.save()
        if save or changed:
            self.state_changed.emit()

    def toggle_dj(self) -> None:
        self.cfg.dj_mode = not self.cfg.dj_mode
        if self.engine is not None and self._next_info is not None:
            self.engine.clear_next()
            self._next_info = self._next_src = None
        self.cfg.save()
        self.set_eq(self.cfg.eq_preset, save=False)
        self.toast.emit("Modo DJ: fundidos, volumen nivelado y radio infinita"
                        if self.cfg.dj_mode else "Modo DJ desactivado", False)

    def remove_from_queue(self, rows: list[int]) -> None:
        for r in sorted(set(rows), reverse=True):
            self.queue.remove(r)
        self.queue_changed.emit()

    def clear_queue(self) -> None:
        self.queue.clear(keep_current=not self.idle())
        self.queue_changed.emit()

    # ================================================================ biblioteca
    def rescan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.scanning.emit(True)

        def done(_n) -> None:  # noqa: ANN001
            self._scanning = False
            self.scanning.emit(False)
            self.library_changed.emit()

        def fail(exc: Exception) -> None:
            self._scanning = False
            self.scanning.emit(False)
            self.toast.emit(f"Error leyendo la biblioteca: {exc}", True)

        workers.run(self.library.scan, done=done, fail=fail)

    def add_folder(self, path: str) -> None:
        p = Path(path).expanduser()
        if not p.is_dir():
            self.toast.emit(f"No existe la carpeta {p}", True)
            return
        f = self.library.add_folder(str(p))
        self.toast.emit(f"«{f.name}» añadida a la biblioteca", False)
        self.library_changed.emit()
        self.rescan()

    # ================================================================ descargas
    def download(self, tracks: list[Track], group: str | None = None) -> None:
        if self.downloader is None:
            self.toast.emit("Un momento, BeatShell aún está arrancando…", False)
            return
        todo = [t for t in tracks if not t.is_local]
        if not todo:
            self.toast.emit("Ya está en tu equipo", False)
            return
        jobs = self.downloader.enqueue(todo, group)
        if group and jobs:
            self._groups.setdefault(jobs[0].group, []).extend(j.id for j in jobs)
        n = len(jobs)
        if n:
            self.toast.emit(f"Descargando {n} canción{'es' if n != 1 else ''} a MP3", False)

    def _job_changed(self, job) -> None:  # noqa: ANN001
        if job.finished and job.path:
            self.playlists.link_local(job.key, job.path)
        self.job_changed.emit(job)
        if job.finished:
            ids = self._groups.get(job.group)
            if ids:
                jobs = [j for j in self.downloader.jobs if j.id in ids]
                if jobs and all(j.finished for j in jobs):
                    bad = sum(1 for j in jobs if j.status == "error")
                    msg = f"«{job.group}» descargada"
                    if bad:
                        msg += f" ({bad} con error)"
                    self.toast.emit(msg, bool(bad))
                    del self._groups[job.group]
            elif job.status != "error":
                self.toast.emit(f"Descargada: {job.track.title}", False)
            self.library_changed.emit()
