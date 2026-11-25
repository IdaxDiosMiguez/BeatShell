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
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import SPOTIFY, Track
from . import Collection

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
EMBED_LIMIT = 100


class SpotifyError(RuntimeError):
    pass


class RateLimited(SpotifyError):
    pass


def _get(url: str, headers: dict[str, str] | None = None, data: bytes | None = None,
         retries: int = 2) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SpotifyError("Spotify no encuentra ese enlace (¿es privado?)") from None
            if exc.code == 429:  # demasiadas peticiones seguidas: esperamos un poco y otra vez
                if attempt < retries:
                    wait = exc.headers.get("Retry-After") if exc.headers else None
                    time.sleep(min(float(wait or 0) or 2.0 * (attempt + 1) ** 2, 10))
                    continue
                raise RateLimited("Spotify está limitando las peticiones: espera un minuto y "
                                  "vuelve a intentarlo") from None
            raise SpotifyError(f"Spotify respondió con error {exc.code}") from None
        except urllib.error.URLError as exc:
            raise SpotifyError(f"No se pudo conectar con Spotify: {exc.reason}") from None
    raise SpotifyError("Spotify no respondió")


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


# ------------------------------------------------------------------ perfiles
# La página pública de un perfil solo trae sus primeras 10 playlists públicas (el resto lo
# carga la web con sesión). Con credenciales de API se leen todas.
PROFILE_LIMIT = 10
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


@dataclass
class Profile:
    id: str
    name: str
    playlists: list[tuple[str, str]] = field(default_factory=list)  # ('playlist:ID', nombre)
    total: int = 0  # playlists públicas que tiene (puede ser más de las que se leyeron)
    thumb: str | None = None  # foto de perfil

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/user/{urllib.parse.quote(self.id)}"


def _profile_page(uid: str) -> Profile:
    html = _get(f"https://open.spotify.com/user/{urllib.parse.quote(uid)}",
                headers={"User-Agent": _MOBILE_UA}).decode("utf-8", "replace")
    name, playlists, total, thumb = uid, [], 0, None
    m = re.search(r'<script id="initialState"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            raw = m.group(1).strip()
            state = json.loads(raw if raw.startswith("{") else base64.b64decode(raw))
            items = state["entities"]["items"]
        except (KeyError, ValueError, TypeError):
            items = {}
        user = items.get(f"spotify:user:{uid}") or next(
            (v for v in items.values() if isinstance(v, dict) and v.get("__typename") == "User"), None)
        if user:
            name = user.get("name") or uid
            thumb = _best_image((user.get("avatar") or {}).get("sources"))
            if thumb and "scdn.co" not in thumb:
                thumb = None  # fotos de Facebook: enlaces firmados que caducan
            pub = user.get("publicPlaylistsV2") or {}
            for it in pub.get("items") or []:
                data = it.get("data") or {}
                uri = data.get("uri") or it.get("_uri") or ""
                if uri.startswith("spotify:playlist:"):
                    playlists.append((f"playlist:{uri.rsplit(':', 1)[1]}", data.get("name") or "Playlist"))
            total = pub.get("totalCount") or len(playlists)
    if not playlists:  # si cambian el formato, al menos los enlaces de la página
        seen = dict.fromkeys(re.findall(r'href="/playlist/([A-Za-z0-9]{22})"', html))
        playlists = [(f"playlist:{pid}", "Playlist") for pid in seen]
        total = max(total, len(playlists))
    if not m and not playlists:
        raise SpotifyError("No se pudo leer ese perfil de Spotify")
    return Profile(id=uid, name=name, playlists=playlists, total=total, thumb=thumb)


def _profile_api(uid: str, client_id: str, secret: str) -> Profile:
    tok = _token(client_id, secret)
    q = urllib.parse.quote(uid)
    user = _api(f"users/{q}", tok)
    page = _api(f"users/{q}/playlists?limit=50", tok)
    playlists = []
    while True:
        for p in page.get("items") or []:
            # La API también devuelve las playlists que sigue: nos quedamos con las suyas.
            if p and p.get("id") and ((p.get("owner") or {}).get("id") or "").lower() == uid.lower():
                playlists.append((f"playlist:{p['id']}", p.get("name") or "Playlist"))
        if not page.get("next"):
            break
        page = _api(page["next"], tok)
    imgs = user.get("images") or []
    return Profile(id=uid, name=user.get("display_name") or uid, playlists=playlists,
                   total=len(playlists), thumb=_best_image(imgs))


def user_playlists(uid: str, client_id: str = "", secret: str = "") -> Profile:
    """Playlists públicas de un perfil (todas con credenciales; si no, las 10 primeras)."""
    if client_id and secret:
        try:
            return _profile_api(uid, client_id, secret)
        except SpotifyError:
            pass
    return _profile_page(uid)


@dataclass
class ImportResult:
    collections: list[Collection]
    profile: Profile | None = None
    failed: list[str] = field(default_factory=list)  # playlists que no se pudieron leer


def import_link(text: str, client_id: str = "", secret: str = "",
                progress: Callable[[int, int, str], None] | None = None) -> ImportResult:
    """Lee lo necesario para importar un enlace de Spotify a la biblioteca.

    Admite playlists, álbumes y perfiles (todas sus playlists públicas). `progress(i, n,
    nombre)` se llama antes de leer cada playlist de un perfil.
    """
    from . import classify

    kind, value = classify(text)
    if kind == "spotify":
        if value.startswith("track:"):
            raise SpotifyError("Ese enlace es de una canción: pega el de una playlist, "
                               "un álbum o un perfil")
        col = fetch(value, client_id, secret)
        if not col.tracks:
            raise SpotifyError("Esa playlist no tiene canciones disponibles")
        fill_covers(col.tracks)
        return ImportResult([col])
    if kind != "spotify_user":
        raise SpotifyError("Pega un enlace de Spotify: de una playlist, un álbum o un perfil")
    prof = user_playlists(value, client_id, secret)
    if not prof.playlists:
        raise SpotifyError(f"«{prof.name}» no tiene playlists públicas")
    out = ImportResult([], profile=prof)
    for i, (ref, name) in enumerate(prof.playlists):
        if progress:
            progress(i, len(prof.playlists), name)
        try:
            col = fetch(ref, client_id, secret)
        except RateLimited:
            time.sleep(3)  # una pausa y se sigue con la siguiente
            out.failed.append(name)
            continue
        except SpotifyError:
            out.failed.append(name)
            continue
        if col.tracks:
            out.collections.append(col)
        else:
            out.failed.append(name)
    if not out.collections:
        raise SpotifyError(f"No se pudo leer ninguna playlist de «{prof.name}»")
    # Las portadas, al final: si Spotify limita las peticiones solo se pierden portadas.
    fill_covers([t for c in out.collections for t in c.tracks])
    return out


def _cover(uri: str, stop: "threading.Event") -> str | None:
    if stop.is_set():
        return None
    q = urllib.parse.quote(uri, safe="")
    try:
        data = json.loads(_get(f"https://open.spotify.com/oembed?url={q}", retries=0))
    except RateLimited:
        stop.set()  # Spotify pide calma: las demás se quedan sin portada propia
        return None
    except (SpotifyError, ValueError):
        return None
    url = data.get("thumbnail_url") or ""
    # Mismo id de imagen en el CDN de siempre y a 640 px.
    m = re.search(r"/image/([0-9a-f]{40})$", url)
    return f"https://i.scdn.co/image/{m.group(1).replace('ab67616d00001e02', 'ab67616d0000b273')}" \
        if m else (url or None)


_COVERS: dict[str, str] | None = None
_COVERS_LOCK = threading.Lock()


def _covers_path():  # noqa: ANN202
    try:
        from ..config import CACHE_DIR

        return CACHE_DIR / "spotify_covers.json"
    except Exception:  # noqa: BLE001 - sin carpeta de caché (p. ej. Android) no se guarda
        return None


def _known_covers() -> dict[str, str]:
    global _COVERS
    with _COVERS_LOCK:
        if _COVERS is None:
            _COVERS = {}
            path = _covers_path()
            try:
                if path is not None and path.exists():
                    _COVERS = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                _COVERS = {}
        return _COVERS


def fill_covers(tracks: list[Track], workers: int = 6) -> None:
    """Pone a cada canción la portada de su álbum, como en Spotify.

    El embed de una playlist no las trae; el oEmbed público de cada canción sí (~0,2 s cada
    una, en paralelo). Se recuerdan en disco para no volver a pedirlas, y si Spotify empieza a
    limitar las peticiones se deja de preguntar: esas canciones se quedan sin portada propia.
    """
    from concurrent.futures import ThreadPoolExecutor

    known = _known_covers()
    todo = []
    for t in tracks:
        if t.thumb or not t.spotify_uri:
            continue
        if t.spotify_uri in known:
            t.thumb = known[t.spotify_uri]
        else:
            todo.append(t)
    if not todo:
        return
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = list(pool.map(lambda t: _cover(t.spotify_uri, stop), todo))
    new = {t.spotify_uri: url for t, url in zip(todo, found) if url}
    for t in todo:
        t.thumb = new.get(t.spotify_uri)
    if new:
        with _COVERS_LOCK:
            known.update(new)
            path = _covers_path()
            try:
                if path is not None:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(known), "utf-8")
                    tmp.replace(path)
            except OSError:
                pass
