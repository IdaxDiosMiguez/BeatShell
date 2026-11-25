"""YouTube se lee en un proceso aparte que se puede apagar.

Leer YouTube con yt-dlp deja en memoria más de 100 MB (el reproductor JavaScript de
YouTube, las respuestas de la página, su caché…) que Python ya no devuelve al sistema.
Aquí ese trabajo se hace en un proceso hijo: mientras lo usas está despierto; cuando
lleva un rato sin hacer nada se apaga y su memoria vuelve entera al sistema. La
siguiente vez que haga falta arranca de nuevo (medio segundo).

Todo pasa por `youtube._extract`, así que basta con sustituir esa función: el resto
del núcleo (búsquedas, playlists, streams, emparejado de Spotify) no se entera.
"""

from __future__ import annotations

import importlib.util
import itertools
import multiprocessing as mp
import sys
import threading
import time
import types
from concurrent.futures import Future
from typing import Any

# Claves que yt-dlp devuelve y BeatShell no usa: no hace falta copiarlas entre procesos.
_DROP = {"formats", "automatic_captions", "subtitles", "heatmap", "chapters", "storyboards",
         "_format_sort_fields", "requested_subtitles", "__post_extractor", "description", "tags",
         "categories", "comments"}


def _slim(info: Any) -> Any:
    if isinstance(info, dict):
        out = {k: _slim(v) for k, v in info.items() if k not in _DROP and not callable(v)}
        if "requested_formats" in out:
            out["requested_formats"] = [
                {k: f.get(k) for k in ("url", "available_at", "http_headers", "ext", "acodec")}
                for f in out["requested_formats"] or []]
        return out
    if isinstance(info, list):
        return [_slim(v) for v in info]
    return info


# ====================================================================== proceso hijo
def _serve(conn) -> None:  # noqa: ANN001
    import os
    from concurrent.futures import ThreadPoolExecutor

    try:  # el hijo nunca escribe en la consola
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    except OSError:
        pass
    from BeatShell.sources import youtube

    real = youtube._extract
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="extract")

    def handle(rid: int, url: str, opts: dict) -> None:
        try:
            msg = (rid, True, _slim(real(url, **opts)))
        except BaseException as exc:  # noqa: BLE001
            err = exc if isinstance(exc, youtube.YouTubeError) else RuntimeError(str(exc)[:300])
            msg = (rid, False, err)
        with lock:
            try:
                conn.send(msg)
            except (OSError, ValueError):
                pass

    conn.send((0, True, "ready"))
    while True:
        try:
            req = conn.recv()
        except (EOFError, OSError):
            break
        if req is None:
            break
        pool.submit(handle, *req)
    os._exit(0)


# ====================================================================== proxy
class Extractor:
    """Sustituto de `youtube._extract` que delega en el proceso hijo."""

    def __init__(self, idle_timeout: float = 300.0) -> None:
        self.idle_timeout = idle_timeout
        self._lock = threading.RLock()
        self._proc = None
        self._conn = None
        self._pending: dict[int, Future] = {}
        self._ids = itertools.count(1)
        self._last_used = time.monotonic()
        self._ready = threading.Event()
        threading.Thread(target=self._reaper, daemon=True, name="extract-reaper").start()

    # ------------------------------------------------------------ ciclo de vida
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.alive:
                return
            ctx = mp.get_context("spawn")
            parent, child = ctx.Pipe(duplex=True)
            self._ready.clear()
            self._proc = ctx.Process(target=_serve, args=(child,), daemon=True, name="beatshell-youtube")
            self._proc.start()
            child.close()
            self._conn = parent
            self._last_used = time.monotonic()
            threading.Thread(target=self._reader, args=(parent,), daemon=True, name="extract-reader").start()

    def stop(self) -> None:
        """Apaga el proceso (si no está trabajando). Su memoria vuelve al sistema."""
        with self._lock:
            if self._pending or not self.alive:
                return
            proc, conn = self._proc, self._conn
            self._proc = self._conn = None
        try:
            conn.send(None)
        except (OSError, ValueError):
            pass
        proc.join(timeout=1.0)
        if proc.is_alive():
            proc.terminate()

    def shutdown(self) -> None:
        with self._lock:
            for fut in self._pending.values():
                fut.cancel()
            self._pending.clear()
        self.stop()

    def _reaper(self) -> None:
        while True:
            time.sleep(2.0)
            if (self.alive and not self._pending
                    and time.monotonic() - self._last_used > self.idle_timeout):
                self.stop()

    def _reader(self, conn) -> None:  # noqa: ANN001
        while True:
            try:
                rid, ok, payload = conn.recv()
            except (EOFError, OSError):
                break
            if rid == 0:
                self._ready.set()
                continue
            with self._lock:
                fut = self._pending.pop(rid, None)
                self._last_used = time.monotonic()
            if fut is None or fut.cancelled():
                continue
            if ok:
                fut.set_result(payload)
            else:
                fut.set_exception(payload)
        # El proceso terminó: lo que estuviera en marcha falla (y se reintentará si hace falta).
        with self._lock:
            if self._conn is conn:
                self._proc = self._conn = None
            dead = [(r, f) for r, f in self._pending.items()]
            for r, _f in dead:
                self._pending.pop(r, None)
        for _r, f in dead:
            if not f.done():
                f.set_exception(RuntimeError("Se reinició el lector de YouTube; vuelve a intentarlo"))

    # ------------------------------------------------------------ llamada
    def __call__(self, url: str, **opts: Any) -> dict[str, Any]:
        fut: Future = Future()
        with self._lock:
            self.start()
            rid = next(self._ids)
            self._pending[rid] = fut
            self._last_used = time.monotonic()
            conn = self._conn
        try:
            conn.send((rid, url, opts))
        except (OSError, ValueError, AttributeError):
            with self._lock:
                self._pending.pop(rid, None)
            raise RuntimeError("No se pudo hablar con el lector de YouTube") from None
        return fut.result(timeout=180)


def lazy_import(name: str) -> None:
    """Importa un módulo sin cargarlo hasta que se use de verdad (yt-dlp: ~45 MB).

    En el proceso principal yt-dlp solo hace falta para descargar MP3.
    """
    if name in sys.modules or importlib.util.find_spec(name) is None:
        return
    stub = types.ModuleType(name)
    stub.__spec__ = None  # `import name` no mira dentro y no fuerza la carga
    real: list[types.ModuleType] = []
    lock = threading.Lock()

    def load() -> types.ModuleType:
        with lock:
            if not real:
                if sys.modules.get(name) is stub:
                    del sys.modules[name]
                real.append(importlib.import_module(name))
            return real[0]

    def __getattr__(attr: str):  # noqa: ANN202, N807 - PEP 562
        return getattr(load(), attr)

    stub.__getattr__ = __getattr__  # type: ignore[attr-defined]
    sys.modules[name] = stub


def install(youtube_module, extractor: Extractor) -> None:  # noqa: ANN001
    youtube_module._extract = extractor
