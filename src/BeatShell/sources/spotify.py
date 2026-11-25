"""Lectura de playlists, álbumes y canciones de Spotify.

Sin configuración usa la página pública de "embed" (no necesita cuenta ni API key).
Spotify solo expone ahí las primeras ~100 canciones; para playlists más grandes se
pueden poner `spotify_client_id` y `spotify_client_secret` en la configuración y
se usará la Web API oficial con paginación.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from ..models import SPOTIFY, Track
from . import Collection

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
EMBED_LIMIT = 100


class SpotifyError(RuntimeError):
    pass


def _get(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SpotifyError("Spotify no encuentra ese enlace (¿es privado?)") from None
        raise SpotifyError(f"Spotify respondió con error {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SpotifyError(f"No se pudo conectar con Spotify: {exc.reason}") from None


def _artists(subtitle: str) -> str:
    return ", ".join(a.strip() for a in subtitle.replace("\xa0", " ").split(",") if a.strip())


def _best_image(sources: list[dict[str, Any]] | None) -> str | None:
    if not sources:
        return None
    url = max(sources, key=lambda s: s.get("width") or 0).get("url") or ""
    # El embed da portadas de 300 px; el mismo id con este prefijo es la de 640 px.
    return url.replace("ab67616d00001e02", "ab67616d0000b273") or None


# ------------------------------------------------------------------ embed
def _from_embed(kind: str, sid: str) -> Collection:
    html = _get(f"https://open.spotify.com/embed/{kind}/{sid}").decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise SpotifyError("Spotify cambió su página; configura credenciales de API")
    try:
        ent = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, ValueError, TypeError):
        raise SpotifyError("No se pudo leer el contenido de Spotify") from None

    name = ent.get("name") or ent.get("title") or "Spotify"
    cover = _best_image((ent.get("coverArt") or {}).get("sources"))
    if not cover and ent.get("visualIdentity"):
        cover = _best_image((ent["visualIdentity"] or {}).get("image"))
    album = name if kind == "album" else ""

    items = ent.get("trackList")
    if items is None:  # una sola canción
        artists = ", ".join(a.get("name", "") for a in ent.get("artists") or [] if a.get("name"))
        items = [{"uri": ent.get("uri"), "title": name,
                  "subtitle": artists or ent.get("subtitle", ""), "duration": ent.get("duration")}]
        album = ""
    tracks = []
    for i, it in enumerate(items, 1):
        if not it.get("title"):
            continue
        dur = it.get("duration")
        tracks.append(
            Track(
                title=it["title"],
                artist=_artists(it.get("subtitle") or ""),
                album=album,
                duration=dur / 1000 if dur else None,
                source=SPOTIFY,
                spotify_uri=it.get("uri"),
                thumb=cover if kind != "playlist" else None,
                track_no=i if kind == "album" else None,
            )
        )
    return Collection(
        name=name,
        tracks=tracks,
        kind="album" if kind == "album" else "playlist",
        source="spotify",
        owner=_artists(ent.get("subtitle") or ""),
        thumb=cover,
        url=f"https://open.spotify.com/{kind}/{sid}",
    )


# ------------------------------------------------------------------ Web API
_TOKEN: tuple[str, float] | None = None


def _token(client_id: str, secret: str) -> str:
    global _TOKEN
    if _TOKEN and _TOKEN[1] > time.time():
        return _TOKEN[0]
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    raw = _get(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=b"grant_type=client_credentials",
    )
    data = json.loads(raw)
    _TOKEN = (data["access_token"], time.time() + data.get("expires_in", 3600) - 60)
    return _TOKEN[0]


def _api(path: str, token: str) -> dict[str, Any]:
    url = path if path.startswith("http") else f"https://api.spotify.com/v1/{path}"
    return json.loads(_get(url, headers={"Authorization": f"Bearer {token}"}))


def _api_track(t: dict[str, Any], album: dict[str, Any] | None = None) -> Track | None:
    if not t or not t.get("name"):
        return None
    alb = album or t.get("album") or {}
    imgs = alb.get("images") or []
    return Track(
        title=t["name"],
        artist=", ".join(a["name"] for a in t.get("artists") or []),
        album=alb.get("name", ""),
        duration=(t.get("duration_ms") or 0) / 1000 or None,
        source=SPOTIFY,
        spotify_uri=t.get("uri"),
        thumb=imgs[0]["url"] if imgs else None,
        track_no=t.get("track_number"),
    )


def _from_api(kind: str, sid: str, client_id: str, secret: str) -> Collection:
    tok = _token(client_id, secret)
    if kind == "track":
        t = _api_track(_api(f"tracks/{sid}", tok))
        return Collection(name=t.title if t else "Canción", tracks=[t] if t else [],
                          kind="playlist", source="spotify", thumb=t.thumb if t else None)
    if kind == "album":
        alb = _api(f"albums/{sid}", tok)
        tracks, page = [], alb.get("tracks") or {}
        while True:
            tracks += [x for t in page.get("items") or [] if (x := _api_track(t, alb))]
            if not page.get("next"):
                break
            page = _api(page["next"], tok)
        imgs = alb.get("images") or []
        return Collection(name=alb.get("name", "Álbum"), tracks=tracks, kind="album",
                          source="spotify", owner=", ".join(a["name"] for a in alb.get("artists", [])),
                          thumb=imgs[0]["url"] if imgs else None,
                          url=f"https://open.spotify.com/album/{sid}")
    pl = _api(f"playlists/{sid}?fields=name,owner(display_name),images", tok)
    fields = urllib.parse.quote("items(track(name,uri,duration_ms,track_number,artists(name),album(name,images))),next")
    page = _api(f"playlists/{sid}/tracks?limit=100&fields={fields}", tok)
    tracks = []
    while True:
        tracks += [x for it in page.get("items") or [] if (x := _api_track(it.get("track")))]
        if not page.get("next"):
            break
        page = _api(page["next"], tok)
    imgs = pl.get("images") or []
    return Collection(name=pl.get("name", "Playlist"), tracks=tracks, kind="playlist",
                      source="spotify", owner=(pl.get("owner") or {}).get("display_name", ""),
                      thumb=imgs[0]["url"] if imgs else None,
                      url=f"https://open.spotify.com/playlist/{sid}")


def fetch(ref: str, client_id: str = "", secret: str = "") -> Collection:
    """`ref` es 'playlist:ID', 'album:ID' o 'track:ID'."""
    kind, sid = ref.split(":", 1)
    if client_id and secret:
        try:
            return _from_api(kind, sid, client_id, secret)
        except SpotifyError:
            pass  # p. ej. playlists editoriales bloqueadas en la API → probamos el embed
    return _from_embed(kind, sid)
