"""Fuentes de música: YouTube, Spotify y detección de enlaces."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

from ..models import Track


@dataclass
class Collection:
    """Una lista de pistas que viene de fuera: playlist o álbum."""

    name: str
    tracks: list[Track] = field(default_factory=list)
    kind: str = "playlist"  # playlist | album | search
    source: str = "youtube"
    owner: str = ""
    thumb: str | None = None
    url: str | None = None


_SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?|spotify:)"
    r"(?P<kind>track|album|playlist)[/:](?P<id>[A-Za-z0-9]{10,})"
)
# Perfiles: los ids nuevos son alfanuméricos; los antiguos son nombres de usuario con . _ -
_SPOTIFY_USER_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?user/|spotify:user:)(?P<id>[^/?#&\s]+)"
)


def classify(text: str) -> tuple[str, str]:
    """Clasifica lo que el usuario escribió en la barra.

    Devuelve (tipo, valor) con tipo en: spotify, spotify_user, youtube_playlist,
    youtube_video, url, search. Para spotify el valor es 'playlist:ID', 'album:ID' o
    'track:ID'; para spotify_user, el id del perfil.
    """
    t = text.strip()
    m = _SPOTIFY_RE.search(t)
    if m:
        return "spotify", f"{m['kind']}:{m['id']}"
    m = _SPOTIFY_USER_RE.search(t)
    if m:
        return "spotify_user", unquote(m["id"])
    if re.match(r"^https?://", t):
        u = urlparse(t)
        host = (u.hostname or "").lower()
        if "youtube" in host or host.endswith("youtu.be"):
            q = parse_qs(u.query)
            if "list" in q and (u.path.startswith("/playlist") or "v" not in q):
                return "youtube_playlist", t
            if "list" in q and not q["list"][0].startswith(("RD", "UL")):
                # vídeo dentro de una playlist: tratamos la playlist completa
                return "youtube_playlist", t
            return "youtube_video", t
        return "url", t
    return "search", t
