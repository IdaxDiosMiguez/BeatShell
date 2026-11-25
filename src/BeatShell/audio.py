"""Motor de audio multiplataforma.

ffmpeg decodifica cualquier fuente (archivo local o stream HTTP de YouTube) a PCM
float32 estéreo, y sounddevice (PortAudio) lo envía a la tarjeta de sonido. Tener
las muestras en nuestras manos permite un visualizador de espectro real (FFT),
control de volumen sin clics, fundidos entre canciones (modo DJ) y funcionar igual
en Windows, macOS y Linux.

El motor maneja hasta dos "voces" a la vez: la canción principal y, durante un
fundido cruzado, la que se está despidiendo.
"""

from __future__ import annotations

import collections
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .ffmpeg import POPEN_FLAGS, ffmpeg_exe

try:  # PortAudio puede faltar en Linux (paquete libportaudio2)
    import sounddevice as sd

    _SD_ERROR: Exception | None = None
except OSError as exc:  # pragma: no cover - depende del sistema
    sd = None
    _SD_ERROR = exc

CHANNELS = 2
BYTES_PER_FRAME = 4 * CHANNELS
READ_FRAMES = 4096
MAX_BUFFER_SECONDS = 12.0
PREBUFFER_SECONDS = 0.2
VIZ_SIZE = 2048
CLICK_FADE = 0.04  # fundido mínimo al empezar o saltar, evita clics


@dataclass
class Source:
    """Algo que ffmpeg sabe abrir."""

    input: str
    headers: dict[str, str] = field(default_factory=dict)
    duration: float | None = None
    # YouTube no sirve el audio hasta que "termina" el anuncio previo (timestamp unix).
    available_at: float = 0.0
    # Stream de arranque rápido (calidad algo menor): se cambia a alta calidad en marcha.
    fast: bool = False

    @property
    def is_remote(self) -> bool:
        return self.input.startswith(("http://", "https://"))


class AudioError(RuntimeError):
    pass


def _output_samplerate() -> int:
    try:
        rate = int(sd.query_devices(kind="output")["default_samplerate"])
        if 8000 <= rate <= 192000:
            return rate
    except Exception:
        pass
    return 48000


class _Voice:
    """Una canción decodificándose: su búfer, su posición y su envolvente de volumen."""

    def __init__(self, source: Source) -> None:
        self.source = source
        self.token = 0  # cambia en cada reinicio (seek, cambio de filtros)
        self.alive = True
        self.chunks: collections.deque[np.ndarray] = collections.deque()
        self.offset = 0
        self.buffered = 0
        self.base = 0.0
        self.played = 0
        self.ready = False
        self.decoder_done = False
        self.error: str | None = None
        # envolvente: de g0 a g1 en `fade_len` frames
        self.g0 = 0.0
        self.g1 = 1.0
        self.fade_len = 1
        self.fade_pos = 0

    def reset(self, start: float) -> int:
        self.token += 1
        self.chunks.clear()
        self.offset = self.buffered = 0
        self.base = start
        self.played = 0
        self.ready = False
        self.decoder_done = False
        self.error = None
        return self.token

    def set_fade(self, g0: float, g1: float, frames: int) -> None:
        self.g0, self.g1 = g0, g1
        self.fade_len = max(1, frames)
        self.fade_pos = 0

    @property
    def fade_done(self) -> bool:
        return self.fade_pos >= self.fade_len

    def envelope(self, n: int, advance: bool = True) -> np.ndarray | float:
        if self.fade_pos >= self.fade_len:
            return self.g1
        idx = np.arange(self.fade_pos, self.fade_pos + n, dtype=np.float32)
        t = np.minimum(idx / self.fade_len, 1.0)
        # curva de igual potencia: el volumen percibido se mantiene durante el fundido
        if self.g1 > self.g0:
            g = self.g0 + (self.g1 - self.g0) * np.sin(t * np.pi / 2)
        else:
            g = self.g1 + (self.g0 - self.g1) * np.cos(t * np.pi / 2)
        if advance:
            self.fade_pos += n
        return g[:, None]

    def read(self, out: np.ndarray) -> int:
        """Copia hasta len(out) frames. Devuelve cuántos copió."""
        frames, pos = len(out), 0
        while pos < frames and self.chunks:
            chunk = self.chunks[0]
            n = min(len(chunk) - self.offset, frames - pos)
            out[pos : pos + n] = chunk[self.offset : self.offset + n]
            pos += n
            self.offset += n
            if self.offset >= len(chunk):
                self.chunks.popleft()
                self.offset = 0
        self.buffered -= pos
        self.played += pos
        return pos

    @property
    def exhausted(self) -> bool:
        return self.decoder_done and self.buffered <= 0


class AudioEngine:
    def __init__(self, volume: int = 70) -> None:
        if sd is None:
            raise AudioError(
                "No se pudo cargar PortAudio. En Linux instala el paquete "
                "'libportaudio2' (Debian/Ubuntu) o 'portaudio' (Arch/Fedora).\n"
                f"Detalle: {_SD_ERROR}"
            )
        self.samplerate = _output_samplerate()
        self._cond = threading.Condition()
        self._main: _Voice | None = None
        self._out: _Voice | None = None  # la que se despide durante un fundido
        self._pending: _Voice | None = None  # versión de alta calidad esperando su turno
        self._next: _Voice | None = None  # siguiente canción, pre-decodificada (sin hueco)
        self._advanced = False
        self.upgrade_state: str | None = None  # pending | done | failed
        self._paused = False
        self._finished = False
        self._last_error: str | None = None
        self._filters = ""  # cadena -af de ffmpeg (ecualizador, nivelado…)
        self._gain = 0.0
        self._target_gain = 0.0
        self.volume = volume
        self._viz = np.zeros(VIZ_SIZE, dtype=np.float32)
        self._mix = np.zeros((4096, CHANNELS), dtype=np.float32)
        self._tmp = np.zeros((4096, CHANNELS), dtype=np.float32)
        self._stream = None
        self.underruns = 0  # veces que la tarjeta se quedó sin datos (cortes audibles)
        self._open_stream()

    # ------------------------------------------------------------ dispositivo
    def _open_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=1024,
            latency="high",
            callback=self._callback,
        )
        self._stream.start()

    def _ensure_stream(self) -> None:
        """Reabre la salida si el dispositivo cambió o el stream murió."""
        try:
            alive = self._stream is not None and self._stream.active
        except Exception:
            alive = False
        if not alive:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            self.samplerate = _output_samplerate()
            self._open_stream()

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
            self._stream = None

    # ---------------------------------------------------------------- volumen
    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, value: int) -> None:
        self.volume = value

    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        # Curva perceptual: el oído percibe el volumen de forma logarítmica.
        self._target_gain = (self._volume / 100.0) ** 2

    # ---------------------------------------------------------------- filtros
    @property
    def filters(self) -> str:
        return self._filters

    def set_filters(self, chain: str) -> None:
        """Cambia la cadena de filtros de ffmpeg (EQ, nivelado) al vuelo."""
        if chain == self._filters:
            return
        self._filters = chain
        self._cancel_upgrade("failed")
        if self._main is not None and not self._main.exhausted:
            # Se reinicia el decodificador en el punto actual: el cambio se oye ya,
            # no cuando se vacíe el búfer.
            self._restart(self._main, self.position, fade=CLICK_FADE)

    # ------------------------------------------------------------- transporte
    def play(self, source: Source, start: float = 0.0) -> None:
        self._ensure_stream()
        with self._cond:
            self._drop(self._out)
            self._out = None
            self._cancel_upgrade(None)
            self._drop(self._next)
            self._next = None
            self._drop(self._main)
            self._main = _Voice(source)
            self._paused = False
            self._finished = False
            self._last_error = None
        self._restart(self._main, max(0.0, start), fade=CLICK_FADE)

    def crossfade_to(self, source: Source, seconds: float) -> None:
        """Empieza `source` mientras la actual se desvanece (modo DJ)."""
        if self._main is None or self._paused or seconds <= 0:
            self.play(source)
            return
        self._ensure_stream()
        frames = int(seconds * self.samplerate)
        with self._cond:
            self._drop(self._out)
            self._cancel_upgrade(None)
            self._drop(self._next)
            self._next = None
            old = self._main
            old.set_fade(old.g1 if old.fade_done else 1.0, 0.0, frames)
            self._out = old
            self._main = _Voice(source)
            self._finished = False
        self._restart(self._main, 0.0, fade=seconds)

    def upgrade(self, source: Source, lead: float = 2.0) -> bool:
        """Prepara `source` (misma canción, mejor calidad) para sustituir a la actual
        sin cortes dentro de `lead` segundos."""
        main = self._main
        if main is None or self._out is not None:
            return False
        target = self.position + lead
        dur = self.duration
        if dur and target > dur - 3:
            return False
        with self._cond:
            self._cancel_upgrade(None)
            v = _Voice(source)
            self._pending = v
            self.upgrade_state = "pending"
        self._restart(v, target, fade=0.03)
        return True

    def set_next(self, source: Source) -> None:
        """Pre-decodifica la siguiente canción para empalmarla sin silencio."""
        with self._cond:
            self._drop(self._next)
            v = _Voice(source)
            self._next = v
        self._restart(v, 0.0, fade=0.0)

    def clear_next(self) -> None:
        with self._cond:
            self._drop(self._next)
            self._next = None

    def poll_advanced(self) -> bool:
        """True una vez cuando se empalmó la siguiente canción."""
        if self._advanced:
            self._advanced = False
            return True
        return False

    def _cancel_upgrade(self, state: str | None) -> None:
        if self._pending is not None:
            self._drop(self._pending)
            self._pending = None
            self.upgrade_state = state

    @property
    def is_fast(self) -> bool:
        """¿Suena todavía el stream de arranque rápido?"""
        return self._main is not None and self._main.source.fast

    def seek(self, seconds: float) -> None:
        v = self._main
        if v is None:
            return
        self._cancel_upgrade("failed")
        dur = self.duration
        if dur:
            seconds = min(seconds, max(0.0, dur - 0.5))
        self._restart(v, max(0.0, seconds), fade=CLICK_FADE)

    def seek_relative(self, delta: float) -> None:
        self.seek(self.position + delta)

    def toggle_pause(self) -> bool:
        if self._main is not None:
            self._paused = not self._paused
        return self._paused

    def set_paused(self, paused: bool) -> None:
        if self._main is not None:
            self._paused = paused

    def stop(self) -> None:
        with self._cond:
            self._drop(self._out)
            self._drop(self._main)
            self._cancel_upgrade(None)
            self._drop(self._next)
            self._out = self._main = self._next = None
            self._cond.notify_all()
        self._viz = np.zeros(VIZ_SIZE, dtype=np.float32)

    def _drop(self, v: _Voice | None) -> None:
        if v is not None:
            v.alive = False
            v.token += 1
            v.chunks.clear()
            v.buffered = 0

    # ----------------------------------------------------------------- estado
    @property
    def position(self) -> float:
        v = self._main
        return v.base + v.played / self.samplerate if v else 0.0

    @property
    def duration(self) -> float | None:
        v = self._main
        return v.source.duration if v else None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active(self) -> bool:
        return self._main is not None

    @property
    def crossfading(self) -> bool:
        return self._out is not None

    @property
    def buffering(self) -> bool:
        v = self._main
        return v is not None and not v.ready and not self._paused

    @property
    def waiting(self) -> float:
        """Segundos que faltan para que YouTube permita empezar (0 si nada)."""
        v = self._main
        if v is None or v.ready:
            return 0.0
        return max(0.0, v.source.available_at - time.time())

    @property
    def error(self) -> str | None:
        v = self._main
        return v.error if v else self._last_error

    def poll_finished(self) -> bool:
        """True una sola vez cuando la pista principal termina de sonar."""
        if self._finished:
            self._finished = False
            return True
        return False

    def viz_samples(self) -> np.ndarray:
        return self._viz

    # ------------------------------------------------------------ decodificar
    def _restart(self, v: _Voice, start: float, fade: float) -> None:
        with self._cond:
            if not v.alive:
                return
            token = v.reset(start)
            v.set_fade(0.0, 1.0, int(fade * self.samplerate))
            self._finished = False
            self._cond.notify_all()
        threading.Thread(
            target=self._decode, args=(v, token, v.source, start, self._filters),
            daemon=True, name="decoder",
        ).start()

    def _build_cmd(self, src: Source, start: float, filters: str) -> list[str]:
        cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error"]
        if src.is_remote:
            cmd += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_on_network_error", "1",
                "-reconnect_delay_max", "5",
            ]
            headers = dict(src.headers)
            ua = headers.pop("User-Agent", None)
            if ua:
                cmd += ["-user_agent", ua]
            if headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", src.input, "-vn", "-sn", "-dn"]
        if filters:
            cmd += ["-af", filters]
        cmd += [
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ac", str(CHANNELS), "-ar", str(self.samplerate),
            "pipe:1",
        ]
        return cmd

    def _decode(self, v: _Voice, token: int, src: Source, start: float, filters: str) -> None:
        def current() -> bool:
            return v.alive and v.token == token

        while current() and time.time() < src.available_at:
            time.sleep(0.1)
        if not current():
            return
        try:
            proc = subprocess.Popen(
                self._build_cmd(src, start, filters),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **POPEN_FLAGS,
            )
        except OSError as exc:
            with self._cond:
                if current():
                    v.error = f"No se pudo iniciar ffmpeg: {exc}"
                    v.decoder_done = v.ready = True
            return

        err_lines: collections.deque[str] = collections.deque(maxlen=6)

        def drain_stderr() -> None:
            for raw in iter(proc.stderr.readline, b""):
                err_lines.append(raw.decode("utf-8", "replace").strip())

        threading.Thread(target=drain_stderr, daemon=True).start()

        max_frames = int(MAX_BUFFER_SECONDS * self.samplerate)
        prebuffer = int(PREBUFFER_SECONDS * self.samplerate)
        total = 0
        leftover = b""
        try:
            while current():
                data = proc.stdout.read(READ_FRAMES * BYTES_PER_FRAME)
                if not data:
                    break
                if leftover:
                    data = leftover + data
                cut = len(data) - (len(data) % BYTES_PER_FRAME)
                data, leftover = data[:cut], data[cut:]
                if not data:
                    continue
                arr = np.frombuffer(data, dtype=np.float32).reshape(-1, CHANNELS)
                total += len(arr)
                with self._cond:
                    while v.buffered >= max_frames and current():
                        self._cond.wait(0.25)
                    if not current():
                        break
                    v.chunks.append(arr)
                    v.buffered += len(arr)
                    if v.buffered >= prebuffer:
                        v.ready = True
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            with self._cond:
                if current():
                    v.decoder_done = True
                    v.ready = True
                    if total == 0:
                        msg = " ".join(err_lines) or "ffmpeg no devolvió audio"
                        msg = re.sub(r"\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", msg)
                        msg = re.sub(r"https?://\S+|\S*&\S+=\S+", "", msg)
                        msg = re.sub(r"Error opening input files?:?\s*", "", msg).strip(" .:")
                        v.error = msg[-300:]

    # ------------------------------------------------- callback de PortAudio
    def _render(self, mix: np.ndarray, tmp: np.ndarray) -> int:
        """Mezcla las voces en `mix`. Debe llamarse con el lock tomado."""
        frames = len(mix)
        if not frames:
            return 0
        produced = 0
        main, out = self._main, self._out
        main_ready = main is not None and main.ready
        if main_ready:
            n = main.read(tmp)
            if n:
                mix[:n] += tmp[:n] * main.envelope(n)
                produced = n
        if out is not None:
            tmp.fill(0)
            n = out.read(tmp)
            if n:
                # el fundido de salida avanza solo cuando la nueva ya suena
                mix[:n] += tmp[:n] * out.envelope(n, advance=main_ready)
                produced = max(produced, n)
            if out.exhausted or (main_ready and out.fade_done):
                self._drop(out)
                self._out = None
        if main is not None and main_ready:
            if main.exhausted:
                self._cancel_upgrade("failed")
                nxt = self._next
                if main.error is None and nxt is not None and nxt.ready and nxt.error is None:
                    # Empalme sin hueco: la siguiente ya está decodificada.
                    self._main, self._next = nxt, None
                    self._advanced = True
                    self._drop(main)
                    rest = len(mix) - produced
                    if rest > 0:
                        produced += self._render(mix[produced:], tmp[produced:])
                    return produced
                # Terminó (o falló): lo avisamos una vez y liberamos la voz.
                self._last_error = main.error
                self._main = None
                self._finished = True
            elif main.buffered <= 0 and not main.decoder_done:
                main.ready = False  # nos quedamos sin datos (red lenta): pre-cargar
        return produced

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        if status.output_underflow:
            self.underruns += 1
        if self._paused or (self._main is None and self._out is None):
            outdata.fill(0)
            return
        if len(self._mix) < frames:
            self._mix = np.zeros((frames, CHANNELS), dtype=np.float32)
            self._tmp = np.zeros((frames, CHANNELS), dtype=np.float32)
        mix = self._mix[:frames]
        tmp = self._tmp[:frames]
        mix.fill(0)
        done = 0
        with self._cond:
            pend, main = self._pending, self._main
            if pend is not None and pend.ready and main is not None and main.ready:
                # Cambio a alta calidad justo en el instante en que empieza la nueva voz.
                gap = int(round(pend.base * self.samplerate - (main.base * self.samplerate + main.played)))
                if gap < 0:
                    self._cancel_upgrade("failed")  # llegó tarde: la app lo reintenta
                elif gap < frames:
                    done = self._render(mix[:gap], tmp[:gap])
                    if self._main is main:
                        main.set_fade(1.0, 0.0, int(0.03 * self.samplerate))
                        self._drop(self._out)
                        self._out = main
                        self._main = pend
                        self._pending = None
                        self.upgrade_state = "done"
                    done = gap
            produced = done + self._render(mix[done:], tmp[done:])
            self._cond.notify_all()

        outdata[:] = mix
        if produced:
            mono = mix[:produced].mean(axis=1)
            if produced >= VIZ_SIZE:
                self._viz = mono[-VIZ_SIZE:].copy()
            else:
                self._viz = np.concatenate((self._viz[produced:], mono))
        g0, g1 = self._gain, self._target_gain
        if g0 != g1:
            step = 0.08  # rampa de volumen por bloque
            g1 = min(g1, g0 + step) if g1 > g0 else max(g1, g0 - step)
            outdata *= np.linspace(g0, g1, frames, dtype=np.float32)[:, None]
            self._gain = g1
        else:
            outdata *= g1
        # Limitador suave: el EQ puede pasarse de 0 dBFS; mejor redondear que recortar.
        peak = float(np.max(np.abs(outdata))) if frames else 0.0
        if peak > 0.98:
            np.tanh(outdata, out=outdata)


# ---------------------------------------------------------------- espectro
_WINDOW = np.hanning(VIZ_SIZE).astype(np.float32)
_EDGES_CACHE: dict[tuple[int, int], np.ndarray] = {}


def spectrum_bands(samples: np.ndarray, samplerate: int, bands: int) -> np.ndarray:
    """Devuelve `bands` valores 0..1 en escala logarítmica de frecuencia (40 Hz–14 kHz)."""
    if bands <= 0:
        return np.zeros(0)
    spec = np.abs(np.fft.rfft(samples * _WINDOW)) / (VIZ_SIZE / 4)
    key = (samplerate, bands)
    edges = _EDGES_CACHE.get(key)
    if edges is None:
        hi = min(14000.0, samplerate / 2 - 1)
        freqs = np.geomspace(40.0, hi, bands + 1)
        edges = np.clip((freqs / samplerate * VIZ_SIZE).astype(int), 1, len(spec) - 1)
        for i in range(1, len(edges)):
            edges[i] = max(edges[i], edges[i - 1] + 1)
        edges = np.clip(edges, 1, len(spec))
        _EDGES_CACHE[key] = edges
    out = np.empty(bands, dtype=np.float32)
    for i in range(bands):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        seg = spec[a:b]
        out[i] = seg.max() if len(seg) else 0.0
    # Inclinación: los agudos tienen menos energía, los realzamos para que se vean.
    tilt = np.linspace(0.0, 14.0, bands, dtype=np.float32)
    db = 20 * np.log10(np.maximum(out, 1e-6)) + tilt
    return np.clip((db + 60.0) / 54.0, 0.0, 1.0)
