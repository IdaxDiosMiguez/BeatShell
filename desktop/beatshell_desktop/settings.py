"""Ajustes propios de la versión de escritorio (desktop.json).

Lo que comparten terminal y escritorio (carpeta de descargas, calidad, modo DJ,
Spotify…) sigue en config.json. Aquí solo va lo que no existe en la terminal:
así ninguna de las dos versiones pisa los ajustes de la otra.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from BeatShell.config import DATA_DIR, read_json, write_json_atomic

PATH = DATA_DIR / "desktop.json"


@dataclass
class DesktopSettings:
    theme: str = "auto"                 # clave de style.THEMES
    close_action: str = "background"    # background (seguir sonando) | quit
    background_tip_shown: bool = False  # el aviso de «sigo aquí» se enseña una vez
    animations: bool = True             # barras animadas en la canción que suena
    home_tint: bool = False             # inicio: toda la app con los colores de la portada
    geometry: str = ""                  # tamaño y posición de la ventana (base64)

    @classmethod
    def load(cls) -> "DesktopSettings":
        raw = read_json(PATH, {})
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        if s.close_action not in ("background", "quit"):
            s.close_action = "background"
        return s

    def save(self) -> None:
        try:
            write_json_atomic(PATH, asdict(self))
        except OSError:
            pass
