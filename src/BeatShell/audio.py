"""Motor de audio multiplataforma.

ffmpeg decodifica cualquier fuente (archivo local o stream HTTP de YouTube) a PCM
float32 estéreo, y sounddevice (PortAudio) lo envía a la tarjeta de sonido. Tener
las muestras en nuestras manos permite un visualizador de espectro real (FFT),
control de volumen sin clics y funcionar igual en Windows, macOS y Linux.
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
PREBUFFER_SECONDS = 0.35
VIZ_SIZE = 2048


@dataclass
class Source:
    """Algo que ffmpeg sabe abrir."""

    input: str
    headers: dict[str, str] = field(default_factory=dict)
    duration: float | None = None
    # YouTube no sirve el audio hasta que "termina" el anuncio previo (timestamp unix).
    available_at: float = 0.0

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
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._offset = 0  # posición dentro del primer chunk
        self._buffered = 0  # frames pendientes de reproducir
        self._gen = 0  # cambia en cada play/seek/stop → invalida al decodificador
        self._source: Source | None = None
        self._base = 0.0  # segundo desde el que empezó a decodificar
        self._played = 0  # frames reproducidos desde _base
        self._playing = False
        self._paused = False
        self._ready = False  # prebuffer completo
        self._decoder_done = False
        self._finished = False
        self.error: str | None = None
        self._gain = 0.0
        self._target_gain = 0.0
        self.volume = volume
        self._viz = np.zeros(VIZ_SIZE, dtype=np.float32)
        self._stream = None
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

    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        # Curva perceptual: el oído percibe el volumen de forma logarítmica.
        self._target_gain = (self._volume / 100.0) ** 2

    # ------------------------------------------------------------- transporte
    def play(self, source: Source, start: float = 0.0) -> None:
        self._ensure_stream()
        self._source = source
        self.error = None
        self._paused = False
        self._start_decoder(max(0.0, start))

    def seek(self, seconds: float) -> None:
        if self._source is None:
            return
        dur = self.duration
        if dur:
            seconds = min(seconds, max(0.0, dur - 0.5))
        self._start_decoder(max(0.0, seconds))

    def seek_relative(self, delta: float) -> None:
        self.seek(self.position + delta)

    def toggle_pause(self) -> bool:
        if self._source is not None:
            self._paused = not self._paused
        return self._paused

    def set_paused(self, paused: bool) -> None:
        if self._source is not None:
            self._paused = paused

    def stop(self) -> None:
        with self._cond:
            self._gen += 1
            self._chunks.clear()
            self._offset = self._buffered = 0
            self._playing = False
            self._source = None
            self._played = 0
            self._base = 0.0
            self._cond.notify_all()
        self._viz[:] = 0

    # ----------------------------------------------------------------- estado
    @property
    def position(self) -> float:
        return self._base + self._played / self.samplerate

    @property
    def duration(self) -> float | None:
        return self._source.duration if self._source else None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active(self) -> bool:
        return self._source is not None

    @property
    def buffering(self) -> bool:
        return self._playing and not self._ready and not self._paused

    def poll_finished(self) -> bool:
        """True una sola vez cuando la pista termina de sonar."""
        if self._finished:
            self._finished = False
            return True
        return False

    def viz_samples(self) -> np.ndarray:
        return self._viz

    # ------------------------------------------------------------ decodificar
    def _start_decoder(self, start: float) -> None:
        with self._cond:
            self._gen += 1
            gen = self._gen
            self._chunks.clear()
            self._offset = self._buffered = 0
            self._base = start
            self._played = 0
            self._ready = False
            self._decoder_done = False
            self._finished = False
            self._playing = True
            self._gain = 0.0  # fundido de entrada corto: evita clics al saltar
            self._cond.notify_all()
        threading.Thread(
            target=self._decode, args=(gen, self._source, start), daemon=True, name="decoder"
        ).start()

    def _build_cmd(self, src: Source, start: float) -> list[str]:
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
        cmd += [
            "-i", src.input,
            "-vn", "-sn", "-dn",
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ac", str(CHANNELS), "-ar", str(self.samplerate),
            "pipe:1",
        ]
        return cmd

    @property
    def waiting(self) -> float:
        """Segundos que faltan para que YouTube permita empezar (0 si nada)."""
        if not self._playing or self._source is None:
            return 0.0
        return max(0.0, self._source.available_at - time.time())

    def _decode(self, gen: int, src: Source, start: float) -> None:
        while gen == self._gen and time.time() < src.available_at:
            time.sleep(0.1)
        if gen != self._gen:
            return
        try:
            proc = subprocess.Popen(
                self._build_cmd(src, start),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **POPEN_FLAGS,
            )
        except OSError as exc:
            with self._cond:
                if gen == self._gen:
                    self.error = f"No se pudo iniciar ffmpeg: {exc}"
                    self._decoder_done = True
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
            while gen == self._gen:
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
                    while self._buffered >= max_frames and gen == self._gen:
                        self._cond.wait(0.25)
                    if gen != self._gen:
                        break
                    self._chunks.append(arr)
                    self._buffered += len(arr)
                    if self._buffered >= prebuffer:
                        self._ready = True
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            with self._cond:
                if gen == self._gen:
                    self._decoder_done = True
                    self._ready = True
                    if total == 0:
                        msg = " ".join(err_lines) or "ffmpeg no devolvió audio"
                        msg = re.sub(r"\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", msg)
                        msg = re.sub(r"https?://\S+|\S*&\S+=\S+", "", msg)
                        msg = re.sub(r"Error opening input files?:?\s*", "", msg).strip(" .:")
                        self.error = msg[-300:]

    # ------------------------------------------------- callback de PortAudio
    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        if not self._playing or self._paused or not self._ready:
            outdata.fill(0)
            return
        pos = 0
        with self._cond:
            while pos < frames and self._chunks:
                chunk = self._chunks[0]
                n = min(len(chunk) - self._offset, frames - pos)
                outdata[pos : pos + n] = chunk[self._offset : self._offset + n]
                pos += n
                self._offset += n
                if self._offset >= len(chunk):
                    self._chunks.popleft()
                    self._offset = 0
            self._buffered -= pos
            self._played += pos
            done = self._decoder_done and self._buffered <= 0
            if pos:
                self._cond.notify()
        if pos < frames:
            outdata[pos:].fill(0)
            if done and self._playing:
                self._playing = False
                self._finished = True
            elif not done:
                # Nos quedamos sin datos (red lenta): volvemos a pre-cargar.
                self._ready = False
        if pos:
            mono = outdata[:pos].mean(axis=1)
            if pos >= VIZ_SIZE:
                self._viz = mono[-VIZ_SIZE:].copy()
            else:
                self._viz = np.concatenate((self._viz[pos:], mono))
            g0, g1 = self._gain, self._target_gain
            if g0 != g1:
                step = 0.08  # rampa de ganancia por bloque
                g1 = min(g1, g0 + step) if g1 > g0 else max(g1, g0 - step)
                outdata[:pos] *= np.linspace(g0, g1, pos, dtype=np.float32)[:, None]
                self._gain = g1
            else:
                outdata[:pos] *= g1


# ---------------------------------------------------------------- espectro
_WINDOW = np.hanning(VIZ_SIZE).astype(np.float32)
_EDGES_CACHE: dict[tuple[int, int], np.ndarray] = {}


def spectrum_bands(samples: np.ndarray, samplerate: int, bands: int) -> np.ndarray:
    """Devuelve `bands` valores 0..1 en escala logarítmica de frecuencia (40 Hz–16 kHz)."""
    if bands <= 0:
        return np.zeros(0)
    spec = np.abs(np.fft.rfft(samples * _WINDOW)) / (VIZ_SIZE / 4)
    key = (samplerate, bands)
    edges = _EDGES_CACHE.get(key)
    if edges is None:
        hi = min(16000.0, samplerate / 2 - 1)
        freqs = np.geomspace(40.0, hi, bands + 1)
        edges = np.clip((freqs / samplerate * VIZ_SIZE).astype(int), 1, len(spec) - 1)
        # cada banda al menos un bin, sin solaparse hacia atrás
        for i in range(1, len(edges)):
            edges[i] = max(edges[i], edges[i - 1] + 1)
        edges = np.clip(edges, 1, len(spec))
        _EDGES_CACHE[key] = edges
    out = np.empty(bands, dtype=np.float32)
    for i in range(bands):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        seg = spec[a:b]
        out[i] = seg.max() if len(seg) else 0.0
    # Ligera inclinación: los agudos tienen menos energía, los realzamos.
    tilt = np.linspace(0.0, 9.0, bands, dtype=np.float32)
    db = 20 * np.log10(np.maximum(out, 1e-6)) + tilt
    return np.clip((db + 62.0) / 58.0, 0.0, 1.0)


def wait_until(pred, timeout: float, step: float = 0.02) -> bool:  # pragma: no cover - util de test
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return pred()
