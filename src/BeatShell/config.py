"""Rutas de datos por sistema operativo y configuración persistente."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_data_dir, user_music_dir

from . import APP_NAME

# BEATSHELL_HOME permite usar una carpeta de datos alternativa (pruebas, modo portable).
_HOME = os.environ.get("BEATSHELL_HOME")
DATA_DIR = Path(_HOME) if _HOME else Path(user_data_dir(APP_NAME, appauthor=False))
CACHE_DIR = Path(_HOME) / "cache" if _HOME else Path(user_cache_dir(APP_NAME, appauthor=False))
THUMB_DIR = CACHE_DIR / "covers"

for _d in (DATA_DIR, CACHE_DIR, THUMB_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _default_music_dir() -> Path:
    try:
        base = Path(user_music_dir())
    except Exception:  # pragma: no cover - depende del sistema
        base = Path.home() / "Music"
    return base


def default_download_dir() -> str:
    return str(_default_music_dir() / APP_NAME)


def write_json_atomic(path: Path, data: Any) -> None:
    """Escribe JSON sin dejar archivos corruptos si el proceso muere a mitad."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


_SAVE_LOCK = threading.Lock()


@dataclass
class Config:
    volume: int = 70
    shuffle: bool = False
    repeat: str = "off"  # off | all | one
    theme: str = "beatshell"
    download_dir: str = field(default_factory=default_download_dir)
    # "0" = VBR V0 (~245 kbps, transparente) · "320" = CBR 320 kbps
    mp3_quality: str = "0"
    concurrent_downloads: int = 2
    pixel_covers: bool = True
    sidebar: str = "auto"  # auto (según el ancho) | show | hide — se cambia con Ctrl+B
    eq_preset: str = "Plano"
    eq_custom: list = field(default_factory=lambda: [0.0] * 10)  # dB por banda (Personalizado)
    dj_mode: bool = False
    dj_crossfade: float = 8.0  # segundos de fundido entre canciones en modo DJ
    # auto: imagen real en Kitty/Ghostty, bloques en el resto · kitty: forzar · bloques: nunca
    cover_renderer: str = "auto"
    visualizer: bool = True  # false en equipos modestos: ahorra CPU
    # Opcional: credenciales de la API de Spotify para playlists de más de 100 canciones.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    PATH = DATA_DIR / "config.json"

    @classmethod
    def load(cls) -> "Config":
        raw = read_json(cls.PATH, {})
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        cfg.volume = max(0, min(100, int(cfg.volume)))
        if cfg.repeat not in ("off", "all", "one"):
            cfg.repeat = "off"
        return cfg

    def save(self) -> None:
        data = asdict(self)
        with _SAVE_LOCK:
            write_json_atomic(self.PATH, data)
