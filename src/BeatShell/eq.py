"""Ecualizador gráfico de 10 bandas (presets) y filtros del modo DJ."""

from __future__ import annotations

BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
BAND_LABELS = ("31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")

# Ganancias en dB por banda.
PRESETS: dict[str, tuple[float, ...]] = {
    "Plano":        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Graves+":      (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
    "Agudos+":      (0, 0, 0, 0, 0, 1, 2, 4, 5, 6),
    "Rock":         (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "Pop":          (-1, 1, 3, 4, 3, 0, -1, -1, 1, 2),
    "Electrónica":  (6, 5, 2, 0, -2, 0, 1, 3, 4, 5),
    "Hip-hop":      (6, 5, 3, 1, -1, -1, 1, 1, 2, 3),
    "Reggaetón":    (6, 6, 3, 0, -1, 0, 1, 2, 3, 3),
    "Jazz":         (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Clásica":      (4, 3, 2, 1, -1, -1, 0, 2, 3, 4),
    "Acústica":     (3, 3, 2, 1, 1, 1, 2, 3, 2, 1),
    "Voces":        (-2, -2, -1, 1, 3, 4, 4, 3, 1, 0),
    "Noche":        (5, 4, 2, 0, 0, 0, 0, 1, 3, 4),
}
DEFAULT = "Plano"

PRESET_HINT = {
    "Plano": "Sin cambios: el sonido tal cual",
    "Graves+": "Más pegada en los bajos",
    "Agudos+": "Más brillo y detalle",
    "Rock": "Bajos y agudos marcados, medios recogidos",
    "Pop": "Medios presentes, voces delante",
    "Electrónica": "Sub-bajos profundos y agudos brillantes",
    "Hip-hop": "Bajos gordos, voces claras",
    "Reggaetón": "Mucho bajo para el dembow",
    "Jazz": "Cálido y natural",
    "Clásica": "Amplio, con aire en los agudos",
    "Acústica": "Guitarras y voces con cuerpo",
    "Voces": "Para podcasts y canciones habladas",
    "Noche": "Compensa graves y agudos a volumen bajo",
}

# Modo DJ: nivela el volumen entre canciones para que ninguna suene más fuerte.
DJ_LEVELER = "dynaudnorm=f=300:g=15:p=0.9:m=10"


def gains(name: str) -> tuple[float, ...]:
    return PRESETS.get(name, PRESETS[DEFAULT])


def filter_chain(preset: str, dj: bool = False) -> str:
    """Cadena `-af` de ffmpeg para el preset (y el nivelado del modo DJ)."""
    parts: list[str] = []
    g = gains(preset)
    boost = max(0.0, max(g))
    if any(g):
        if boost:
            # bajamos antes de realzar para no saturar
            parts.append(f"volume=-{boost * 0.7:.1f}dB")
        for freq, gain in zip(BANDS, g):
            if gain:
                parts.append(f"equalizer=f={freq}:t=o:w=1:g={gain}")
    if dj:
        parts.append(DJ_LEVELER)
    return ",".join(parts)
