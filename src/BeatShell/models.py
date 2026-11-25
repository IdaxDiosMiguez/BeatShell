"""Modelo de pista común a todas las fuentes (local, YouTube, Spotify)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

LOCAL = "local"
YOUTUBE = "youtube"
SPOTIFY = "spotify"

SOURCE_ICON = {LOCAL: "♫", YOUTUBE: "▶", SPOTIFY: "●"}
SOURCE_LABEL = {LOCAL: "Local", YOUTUBE: "YouTube", SPOTIFY: "Spotify"}


@dataclass
class Track:
    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = None
    source: str = YOUTUBE
    path: str | None = None  # archivo local
    url: str | None = None  # página de YouTube (o resultado de emparejar Spotify)
    spotify_uri: str | None = None
    thumb: str | None = None  # URL de portada remota
    track_no: int | None = None
    # Identificador único de *esta entrada* (en cola o playlist), no de la canción.
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ------------------------------------------------------------------ util
    @property
    def key(self) -> str:
        """Identidad de la canción (para caché de portadas, coincidencias, etc.)."""
        if self.path:
            return "f:" + hashlib.sha1(self.path.encode("utf-8")).hexdigest()[:16]
        if self.spotify_uri:
            return "s:" + self.spotify_uri.rsplit(":", 1)[-1]
        if self.url:
            vid = youtube_id(self.url)
            return "y:" + (vid or hashlib.sha1(self.url.encode()).hexdigest()[:16])
        return "t:" + hashlib.sha1(f"{self.artist}|{self.title}".encode()).hexdigest()[:16]

    @property
    def is_local(self) -> bool:
        return bool(self.path) and Path(self.path).exists()

    @property
    def display_artist(self) -> str:
        return self.artist or "Artista desconocido"

    @property
    def search_query(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def icon(self) -> str:
        if self.is_local:
            return SOURCE_ICON[LOCAL]
        return SOURCE_ICON.get(self.source, "♪")

    def copy(self) -> "Track":
        """Copia con uid nuevo (para insertar la misma canción dos veces)."""
        d = self.to_dict()
        d.pop("uid", None)
        return Track.from_dict(d)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Track":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def youtube_id(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import parse_qs, urlparse

    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        return u.path.strip("/").split("/")[0] or None
    if "youtube" in host:
        q = parse_qs(u.query)
        if "v" in q:
            return q["v"][0]
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            return parts[1]
    return None


def fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
