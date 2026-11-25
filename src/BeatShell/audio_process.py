"""El motor de audio vive en su propio proceso.

Python solo deja ejecutar un hilo a la vez (GIL). Si el audio comparte proceso con la
interfaz, cada vez que la interfaz se dibuja la tarjeta de sonido puede quedarse sin
datos y se oye un corte. En un proceso aparte, el audio tiene su propio intérprete y
nunca espera a nadie.

- Órdenes (play, seek, volumen…): por un Pipe, hacia el proceso de audio.
- Estado (posición, pausa, visualizador): en memoria compartida, sin copias ni bloqueos.
- Avisos (terminó, empalmó la siguiente, cambio a HQ): por una cola, etiquetados con
  el número de la última orden para descartar avisos viejos.
"""

from __future__ import annotations

import multiprocessing as mp
import threading
import time

import numpy as np

from .audio import VIZ_SIZE, AudioError, Source

# Índices del estado compartido
POS, DUR, WAIT, RATE, ACTIVE, PAUSED, BUFFERING, XFADE, FAST, SEQ, UPGRADE, UNDERRUNS = range(12)
_UPGRADE = {0.0: None, 1.0: "pending", 2.0: "done", 3.0: "failed"}
_UPGRADE_CODE = {v: k for k, v in _UPGRADE.items()}


# ====================================================================== proceso hijo
def _serve(conn, events, state, viz, volume: int) -> None:  # noqa: ANN001
    # El hijo no debe escribir nunca en la terminal (rompería la interfaz).
    import os

    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    except OSError:
        pass
    try:
        from .audio import AudioEngine as RealEngine

        eng = RealEngine(volume=volume)
    except Exception as exc:  # noqa: BLE001
        events.put(("init_error", 0, str(exc)))
        return
    state[RATE] = eng.samplerate
    events.put(("ready", 0, eng.samplerate))
    viz_view = np.frombuffer(viz, dtype=np.float32)
    stop = threading.Event()
    seq = [0]

    def publish() -> None:
        last_upgrade = None
        while not stop.is_set():
            done = seq[0]  # el estado que escribimos ya refleja al menos esta orden
            dur = eng.duration
            state[POS] = eng.position
            state[DUR] = dur if dur else -1.0
            state[WAIT] = eng.waiting
            state[ACTIVE] = 1.0 if eng.active else 0.0
            state[PAUSED] = 1.0 if eng.paused else 0.0
            state[BUFFERING] = 1.0 if eng.buffering else 0.0
            state[XFADE] = 1.0 if eng.crossfading else 0.0
            state[FAST] = 1.0 if eng.is_fast else 0.0
            state[UPGRADE] = _UPGRADE_CODE.get(eng.upgrade_state, 0.0)
            state[UNDERRUNS] = eng.underruns
            state[SEQ] = done
            viz_view[:] = eng.viz_samples()
            if eng.poll_finished():
                events.put(("finished", done, eng.error))
            if eng.poll_advanced():
                events.put(("advanced", done, None))
            if eng.upgrade_state != last_upgrade:
                last_upgrade = eng.upgrade_state
                events.put(("upgrade", done, last_upgrade))
            time.sleep(1 / 60)

    threading.Thread(target=publish, daemon=True, name="publish").start()
    try:
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                break  # la app se cerró
            n, name, args = msg
            if name == "close":
                break
            try:
                getattr(eng, name)(*args)
            except Exception as exc:  # noqa: BLE001
                events.put(("error", n, f"{name}: {exc}"))
            seq[0] = n
    finally:
        stop.set()
        eng.close()


# ====================================================================== proxy
class AudioEngine:
    """Misma interfaz que audio.AudioEngine, pero el audio corre en otro proceso."""

    def __init__(self, volume: int = 70) -> None:
        ctx = mp.get_context("spawn")
        self._state = ctx.Array("d", 12, lock=False)
        self._viz_raw = ctx.Array("f", VIZ_SIZE, lock=False)
        self._viz = np.frombuffer(self._viz_raw, dtype=np.float32)
        self._events = ctx.Queue()
        child, self._conn = ctx.Pipe(duplex=False)  # (lectura en el hijo, escritura aquí)
        self._proc = ctx.Process(target=_serve, args=(child, self._events, self._state,
                                                      self._viz_raw, volume),
                                 daemon=True, name="beatshell-audio")
        self._proc.start()
        child.close()
        self._lock = threading.Lock()
        self._seq = 0
        self._barrier = 0  # avisos anteriores a esta orden ya no valen
        self._volume = max(0, min(100, int(volume)))
        self._filters = ""
        self._opt: dict[int, float] = {}  # valores optimistas hasta que el hijo confirme
        self._opt_seq = 0
        self._finished = False
        self._advanced = False
        self._error: str | None = None
        self._upgrade: str | None = None
        self.samplerate = 48000
        self.init_error: str | None = None
        self.ready = False

    # -------------------------------------------------------------- arranque
    def wait_ready(self, timeout: float = 15.0) -> None:
        t0 = time.time()
        while not self.ready and self.init_error is None and time.time() - t0 < timeout:
            self.pump(block=0.05)
        if self.init_error:
            raise AudioError(self.init_error)
        if not self.ready:
            raise AudioError("El proceso de audio no arrancó a tiempo")

    def pump(self, block: float = 0.0) -> None:
        """Lee los avisos del proceso de audio (llamar a menudo, desde la app)."""
        while True:
            try:
                kind, seq, data = self._events.get(timeout=block) if block else \
                    self._events.get_nowait()
            except Exception:  # noqa: BLE001 - cola vacía
                return
            block = 0.0
            if kind == "ready":
                self.ready, self.samplerate = True, int(data)
            elif kind == "init_error":
                self.init_error = data
            elif seq < self._barrier:
                continue  # aviso de antes de la última orden de reproducción
            elif kind == "finished":
                self._finished, self._error = True, data
            elif kind == "advanced":
                self._advanced = True
            elif kind == "upgrade":
                self._upgrade = data

    # -------------------------------------------------------------- órdenes
    def _send(self, name: str, *args, barrier: bool = False) -> int:  # noqa: ANN002
        with self._lock:
            self._seq += 1
            n = self._seq
            if barrier:
                self._barrier = n
                self._finished = self._advanced = False
                self._error = None
            try:
                self._conn.send((n, name, args))
            except (OSError, ValueError):
                pass
        return n

    def _get(self, idx: int) -> float:
        if self._opt and self._state[SEQ] < self._opt_seq and idx in self._opt:
            return self._opt[idx]
        return self._state[idx]

    def play(self, source: Source, start: float = 0.0) -> None:
        self._upgrade = None
        self._send("play", source, start, barrier=True)
        self._opt = {ACTIVE: 1, PAUSED: 0, POS: start, BUFFERING: 1, XFADE: 0,
                     FAST: 1 if source.fast else 0, DUR: source.duration or -1}
        self._opt_seq = self._seq

    def crossfade_to(self, source: Source, seconds: float) -> None:
        self._upgrade = None
        self._send("crossfade_to", source, seconds, barrier=True)
        self._opt = {ACTIVE: 1, PAUSED: 0, POS: 0, XFADE: 1, FAST: 1 if source.fast else 0,
                     DUR: source.duration or -1}
        self._opt_seq = self._seq

    def upgrade(self, source: Source, lead: float = 2.0) -> bool:
        self._upgrade = "pending"
        self._send("upgrade", source, lead)
        return True

    def set_next(self, source: Source) -> None:
        self._send("set_next", source)

    def clear_next(self) -> None:
        self._send("clear_next")

    def seek(self, seconds: float) -> None:
        dur = self.duration
        if dur:
            seconds = min(seconds, max(0.0, dur - 0.5))
        seconds = max(0.0, seconds)
        self._send("seek", seconds)
        self._opt = {POS: seconds, BUFFERING: 1}
        self._opt_seq = self._seq

    def seek_relative(self, delta: float) -> None:
        self.seek(self.position + delta)

    def toggle_pause(self) -> bool:
        paused = not self.paused
        self.set_paused(paused)
        return paused

    def set_paused(self, paused: bool) -> None:
        self._send("set_paused", paused)
        self._opt = {PAUSED: 1 if paused else 0}
        self._opt_seq = self._seq

    def stop(self) -> None:
        self._send("stop", barrier=True)
        self._opt = {ACTIVE: 0, POS: 0, XFADE: 0, FAST: 0, BUFFERING: 0}
        self._opt_seq = self._seq
        self._viz[:] = 0

    def set_filters(self, chain: str) -> None:
        if chain != self._filters:
            self._filters = chain
            self._send("set_filters", chain)

    @property
    def filters(self) -> str:
        return self._filters

    @property
    def volume(self) -> int:
        return self._volume

    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        self._send("set_volume", self._volume)

    def close(self) -> None:
        try:
            self._send("close")
            self._proc.join(timeout=1.5)
        except Exception:  # noqa: BLE001
            pass
        if self._proc.is_alive():
            self._proc.terminate()

    # -------------------------------------------------------------- estado
    @property
    def position(self) -> float:
        return self._get(POS)

    @property
    def duration(self) -> float | None:
        d = self._get(DUR)
        return d if d > 0 else None

    @property
    def active(self) -> bool:
        return self._get(ACTIVE) > 0.5

    @property
    def paused(self) -> bool:
        return self._get(PAUSED) > 0.5

    @property
    def buffering(self) -> bool:
        return self._get(BUFFERING) > 0.5

    @property
    def crossfading(self) -> bool:
        return self._get(XFADE) > 0.5

    @property
    def is_fast(self) -> bool:
        return self._get(FAST) > 0.5

    @property
    def waiting(self) -> float:
        return self._get(WAIT)

    @property
    def underruns(self) -> int:
        return int(self._state[UNDERRUNS])

    @property
    def upgrade_state(self) -> str | None:
        return self._upgrade

    @property
    def error(self) -> str | None:
        return self._error

    def poll_finished(self) -> bool:
        if self._finished:
            self._finished = False
            return True
        return False

    def poll_advanced(self) -> bool:
        if self._advanced:
            self._advanced = False
            return True
        return False

    def viz_samples(self) -> np.ndarray:
        return self._viz
