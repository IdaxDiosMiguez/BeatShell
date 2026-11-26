"""Ecualizador de 10 bandas en directo, aplicado en el callback de audio.

Antes el EQ era un filtro de ffmpeg: cada cambio reiniciaba el decodificador (y en YouTube
volvía a pedir el stream), así que tardaba de medio segundo a dos en oírse. Aquí el filtro
se aplica al audio justo antes de mandarlo a la tarjeta, bloque a bloque:

- La curva es la suma de 10 filtros de pico (como un EQ gráfico analógico) y se convierte
  en un FIR de **fase mínima** (cepstrum): suena como un EQ clásico, sin el retardo ni el
  pre-eco de un FIR de fase lineal.
- Se aplica por FFT con *overlap-save*: un bloque de 512 muestras estéreo cuesta dos FFT de
  4096 puntos (~0,1 ms). Con todo a 0 dB no se calcula nada (cero coste).
- Un cambio se diseña fuera del hilo de audio (~1 ms) y se mezcla con el filtro anterior a
  lo largo del bloque siguiente: sin chasquidos aunque arrastres un deslizador.

Memoria: unos pocos arrays de 4096 muestras (≈100 KB). Solo depende de numpy.
"""

from __future__ import annotations

import numpy as np

from .eq import BANDS

TAPS = 3072        # 64 ms a 48 kHz: resolución de sobra para la banda de 31 Hz
NFFT = 4096        # FFT del overlap-save
MAX_BLOCK = NFFT - TAPS + 1  # 1025: bloques más grandes se procesan por partes
_DESIGN = 8192     # rejilla del diseño (cepstrum): sobra para un FIR de 3072 coeficientes
_Q = 1.41          # una octava por banda, como un EQ gráfico


def _peaking_db(freqs: np.ndarray, fs: float, f0: float, gain_db: float) -> np.ndarray:
    """Respuesta en dB de un filtro de pico RBJ (Audio EQ Cookbook)."""
    if not gain_db or f0 >= fs / 2:
        return np.zeros_like(freqs)
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / fs
    alpha = np.sin(w0) / (2 * _Q)
    b = np.array([1 + alpha * a, -2 * np.cos(w0), 1 - alpha * a])
    den = np.array([1 + alpha / a, -2 * np.cos(w0), 1 - alpha / a])
    z = np.exp(-1j * 2 * np.pi * freqs / fs)
    h = (b[0] + b[1] * z + b[2] * z * z) / (den[0] + den[1] * z + den[2] * z * z)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


def response_db(gains: tuple[float, ...] | list[float], freqs: np.ndarray, fs: float) -> np.ndarray:
    """Curva total en dB (también la dibuja la interfaz)."""
    out = np.zeros_like(freqs, dtype=np.float64)
    for f0, g in zip(BANDS, gains):
        out += _peaking_db(freqs, fs, f0, float(g))
    return out


def headroom_db(gains) -> float:  # noqa: ANN001
    """Bajada previa para que los realces no saturen (el limitador hace el resto)."""
    return -0.6 * max(0.0, max(gains, default=0.0))


class _BandCache:
    """Curva en dB de cada banda en la rejilla del diseño. Al arrastrar una banda solo se
    recalcula esa (las otras nueve se reutilizan): el diseño pasa de ~1,3 ms a ~0,3 ms."""

    def __init__(self, fs: float) -> None:
        self.fs = fs
        self.freqs = np.fft.rfftfreq(_DESIGN, 1 / fs)
        self.gains = [0.0] * len(BANDS)
        self.curves = [None] * len(BANDS)  # np.ndarray float32, o None si la banda es 0 dB

    def total(self, gains) -> np.ndarray:  # noqa: ANN001
        out = np.zeros(len(self.freqs), dtype=np.float64)
        for i, (f0, g) in enumerate(zip(BANDS, gains)):
            if g != self.gains[i]:
                self.gains[i] = g
                self.curves[i] = _peaking_db(self.freqs, self.fs, f0, g).astype(np.float32) if g else None
            if self.curves[i] is not None:
                out += self.curves[i]
        return out


_CACHES: dict[float, _BandCache] = {}


def design(gains, fs: float) -> np.ndarray | None:  # noqa: ANN001
    """Espectro (rfft de NFFT puntos) del FIR de fase mínima, o None si es plano."""
    gains = [float(g) for g in gains]
    if not any(abs(g) > 0.05 for g in gains):
        return None
    cache = _CACHES.get(fs)
    if cache is None:
        cache = _CACHES[fs] = _BandCache(fs)
    db = cache.total(gains) + headroom_db(gains)
    log_mag = np.log(10 ** (db / 20))
    # Fase mínima por cepstrum real: doblar la parte anticausal sobre la causal.
    cep = np.fft.irfft(log_mag, _DESIGN)
    fold = np.zeros(_DESIGN)
    fold[0] = 1.0
    fold[1:_DESIGN // 2] = 2.0
    fold[_DESIGN // 2] = 1.0
    h = np.fft.irfft(np.exp(np.fft.rfft(cep * fold)), _DESIGN)[:TAPS]
    tail = TAPS // 4  # cola con fundido: sin rizado por el recorte
    h[-tail:] *= np.cos(np.linspace(0, np.pi / 2, tail)) ** 2
    return np.fft.rfft(h, NFFT).astype(np.complex64)


class LiveEQ:
    """Estado del filtro en el hilo de audio. `set()` desde cualquier hilo; `process()` en el callback."""

    def __init__(self, samplerate: float, channels: int = 2) -> None:
        self.fs = float(samplerate)
        self.channels = channels
        self._H: np.ndarray | None = None          # filtro sonando (None = plano)
        # (versión, filtro): solo lo escribe set(); el callback recuerda qué versión aplicó.
        # Así un cambio que llega mientras suena un bloque nunca se pierde.
        self._next: tuple[int, np.ndarray | None] = (0, None)
        self._applied = 0
        self._hist = np.zeros((TAPS - 1, channels), dtype=np.float32)
        self._buf = np.zeros((NFFT, channels), dtype=np.float32)
        self.gains: tuple[float, ...] = (0.0,) * len(BANDS)

    def set(self, gains) -> None:  # noqa: ANN001
        """Diseña el filtro nuevo (fuera del callback) y lo deja listo para el siguiente bloque."""
        gains = tuple(float(g) for g in gains)[: len(BANDS)]
        gains += (0.0,) * (len(BANDS) - len(gains))
        if gains == self.gains:
            return
        self.gains = gains
        self._next = (self._next[0] + 1, design(gains, self.fs))  # una sola asignación atómica

    @property
    def active(self) -> bool:
        return self._H is not None or self._next[1] is not None

    def _filter(self, H: np.ndarray, n: int) -> np.ndarray:
        X = np.fft.rfft(self._buf, axis=0)
        X *= H[:, None]
        return np.fft.irfft(X, NFFT, axis=0)[TAPS - 1:TAPS - 1 + n]

    def process(self, block: np.ndarray) -> None:
        """Filtra `block` (frames × canales, float32) en el sitio."""
        n_total = len(block)
        for start in range(0, n_total, MAX_BLOCK):
            self._process(block[start:start + MAX_BLOCK])

    def _process(self, x: np.ndarray) -> None:
        n = len(x)
        version, new = self._next
        changed = version != self._applied
        old = self._H
        if not changed and old is None:
            # Plano: solo se recuerda el pasado (para que activar el EQ no dé un salto).
            self._remember(x)
            return
        buf = self._buf
        buf[:TAPS - 1] = self._hist
        buf[TAPS - 1:TAPS - 1 + n] = x
        buf[TAPS - 1 + n:] = 0
        self._remember(x)
        y_old = self._filter(old, n) if old is not None else x.copy()
        if changed:
            self._applied = version
            y_new = self._filter(new, n) if new is not None else x.copy()
            # Fundido de un bloque entre el filtro viejo y el nuevo: sin chasquidos.
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
            x[:] = y_old + (y_new - y_old) * ramp
            self._H = new
        else:
            x[:] = y_old

    def _remember(self, x: np.ndarray) -> None:
        n = len(x)
        if n >= TAPS - 1:
            self._hist[:] = x[n - (TAPS - 1):]
        else:
            self._hist[:-n] = self._hist[n:]
            self._hist[-n:] = x
