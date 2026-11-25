"""YouTube vía yt-dlp: búsqueda, playlists, streams y emparejado de canciones."""

from __future__ import annotations

import re
import shutil
import threading
import time
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..audio import Source
from ..ffmpeg import ffmpeg_exe
from ..models import YOUTUBE, Track, youtube_id
from . import Collection

# Etiquetas que ensucian los títulos de YouTube y no aportan nada.
_NOISE = re.compile(
    r"\s*[\(\[\{][^\)\]\}]*?\b(official|oficial|video|vídeo|videoclip|audio|lyrics?|letra|"
    r"visuali[sz]er|hd|hq|4k|mv|m/v|clip)\b[^\)\]\}]*[\)\]\}]",
    re.I,
)
_BAD_WORDS = (
    "live", "en vivo", "directo", "cover", "karaoke", "instrumental", "remix", "sped up",
    "slowed", "reverb", "8d", "nightcore", "tutorial", "reaction", "lesson", "acoustic",
    "bass boosted", "1 hour", "loop",
)


class YouTubeError(RuntimeError):
    pass


def _js_runtimes() -> dict[str, dict]:
    found = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
    return found or {"deno": {}}


def base_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "js_runtimes": _js_runtimes(),
        "socket_timeout": 20,
        "logger": _SilentLogger(),
    }
    try:
        opts["ffmpeg_location"] = ffmpeg_exe()
    except RuntimeError:
        pass
    opts.update(extra)
    return opts


class _SilentLogger:
    def debug(self, msg: str) -> None:
        pass

    info = warning = debug

    def error(self, msg: str) -> None:
        pass


# Instancias de yt-dlp reutilizables (conexiones abiertas, caché del reproductor de YouTube).
# yt-dlp no es seguro entre hilos: cada instancia la usa un hilo a la vez.
_POOL: dict[str, list[yt_dlp.YoutubeDL]] = {}
_POOL_LOCK = threading.Lock()


def _extract(url: str, **opts: Any) -> dict[str, Any]:
    key = repr(sorted(opts.items()))
    with _POOL_LOCK:
        idle = _POOL.setdefault(key, [])
        ydl = idle.pop() if idle else None
    if ydl is None:
        ydl = yt_dlp.YoutubeDL(base_opts(**opts))
    try:
        info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", str(exc))
        raise YouTubeError(msg.split("\n")[0][:240]) from None
    finally:
        with _POOL_LOCK:
            if len(_POOL[key]) < 4:
                _POOL[key].append(ydl)
    if not info:
        raise YouTubeError("YouTube no devolvió resultados")
    return info


# ------------------------------------------------------------------ títulos
def clean_title(title: str) -> str:
    t = _NOISE.sub("", title or "").strip()
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -|·") or (title or "").strip()


def clean_channel(channel: str) -> str:
    c = (channel or "").strip()
    c = re.sub(r"\s*-\s*Topic$", "", c)
    c = re.sub(r"VEVO$", "", c).strip()
    return c


def split_artist_title(raw_title: str, channel: str) -> tuple[str, str]:
    """'Artista - Canción (Official Video)' → ('Artista', 'Canción')."""
    title = clean_title(raw_title)
    ch = clean_channel(channel)
    for sep in (" - ", " – ", " — "):
        if sep in title:
            a, t = title.split(sep, 1)
            if a and t:
                return a.strip(), t.strip()
    return ch, title


def thumb_for(video_id: str | None) -> str | None:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else None


def _entry_to_track(e: dict[str, Any]) -> Track | None:
    vid = e.get("id")
    if not vid or (e.get("ie_key") and e["ie_key"] != "Youtube"):
        return None  # canales o playlists dentro de resultados
    title = e.get("title") or ""
    if title in ("[Private video]", "[Deleted video]"):
        return None
    artist, name = split_artist_title(title, e.get("channel") or e.get("uploader") or "")
    return Track(
        title=name or title,
        artist=artist,
        duration=float(e["duration"]) if e.get("duration") else None,
        source=YOUTUBE,
        url=f"https://www.youtube.com/watch?v={vid}",
        thumb=thumb_for(vid),
    )


# ------------------------------------------------------------------ búsqueda
def search(query: str, limit: int = 20) -> list[Track]:
    info = _extract(f"ytsearch{limit}:{query}", extract_flat="in_playlist")
    tracks = []
    for e in info.get("entries") or []:
        if e and (t := _entry_to_track(e)):
            tracks.append(t)
    return tracks


def playlist(url: str) -> Collection:
    info = _extract(url, extract_flat="in_playlist", noplaylist=False)
    entries = info.get("entries") or []
    tracks = [t for e in entries if e and (t := _entry_to_track(e))]
    thumbs = info.get("thumbnails") or []
    return Collection(
        name=info.get("title") or "Playlist de YouTube",
        tracks=tracks,
        kind="playlist",
        source="youtube",
        owner=clean_channel(info.get("channel") or info.get("uploader") or ""),
        thumb=thumbs[-1]["url"] if thumbs else (tracks[0].thumb if tracks else None),
        url=url,
    )


# ------------------------------------------------------------------ streams
_STREAM_CACHE: dict[str, tuple[Source, float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_FORMAT = "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best"


def _expiry(url: str) -> float:
    try:
        exp = float(parse_qs(urlparse(url).query)["expire"][0])
        return exp - 600
    except (KeyError, ValueError, IndexError):
        return time.time() + 3 * 3600


# Clientes de YouTube que sirven el audio al momento (sin la espera del anuncio).
# Solo ofrecen formatos mezclados (AAC ~96-128 kbps): se usan para arrancar ya y
# luego el motor cambia, sin cortes, al stream de alta calidad (Opus).
_FAST_CLIENTS = ["tv_simply", "web_embedded"]
_FAST_FORMAT = "bestaudio/18/best[height<=480]/best"


def _cache_key(url: str, fast: bool) -> str:
    return f"{youtube_id(url) or url}|{'fast' if fast else 'hq'}"


def cached_stream(url: str, fast: bool = False) -> Source | None:
    """El stream ya resuelto (si sigue vigente), sin hacer peticiones."""
    with _CACHE_LOCK:
        hit = _STREAM_CACHE.get(_cache_key(url, fast))
    return hit[0] if hit and hit[1] > time.time() else None


def stream_info(url: str, fast: bool = False) -> tuple[Source, dict[str, Any]]:
    """Resuelve la URL directa de audio (con caché hasta que caduca).

    fast=True usa clientes sin espera de anuncio (arranque inmediato, algo menos de calidad).
    """
    key = _cache_key(url, fast)
    with _CACHE_LOCK:
        hit = _STREAM_CACHE.get(key)
        if hit and hit[1] > time.time():
            return hit[0], hit[2]
    if fast:
        # Un cliente cada vez: pedirlos juntos hace que yt-dlp consulte ambos (el doble de lento).
        info, last = None, None
        for client in _FAST_CLIENTS:
            try:
                info = _extract(url, format=_FAST_FORMAT, noplaylist=True,
                                extractor_args={"youtube": {"player_client": [client]}})
                break
            except YouTubeError as exc:
                last = exc
        if info is None:
            raise last or YouTubeError("No hay stream rápido")
    else:
        info = _extract(url, format=_FORMAT, noplaylist=True)
    stream_url = info.get("url")
    if not stream_url and info.get("requested_formats"):
        stream_url = info["requested_formats"][0].get("url")
    if not stream_url:
        raise YouTubeError("No hay un stream de audio disponible para este vídeo")
    src = Source(
        input=stream_url,
        headers=dict(info.get("http_headers") or {}),
        duration=float(info["duration"]) if info.get("duration") else None,
        available_at=float(max([info.get("available_at") or 0] + [
            f.get("available_at") or 0 for f in info.get("requested_formats") or []])),
        fast=fast,
    )
    if fast and src.available_at > time.time() + 0.5:
        raise YouTubeError("El stream rápido también exige esperar")
    meta = {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "id": info.get("id"),
        "thumbnail": info.get("thumbnail"),
        "is_live": info.get("is_live"),
    }
    with _CACHE_LOCK:
        _STREAM_CACHE[key] = (src, _expiry(stream_url), meta)
    return src, meta


def video_track(url: str) -> Track:
    _, meta = stream_info(url)
    artist, name = split_artist_title(meta.get("title") or url, meta.get("channel") or "")
    return Track(
        title=name,
        artist=artist,
        duration=float(meta["duration"]) if meta.get("duration") else None,
        source=YOUTUBE,
        url=f"https://www.youtube.com/watch?v={meta['id']}" if meta.get("id") else url,
        thumb=thumb_for(meta.get("id")),
    )


# ------------------------------------------------------------------ emparejar
def _words(s: str) -> set[str]:
    return set(re.sub(r"[^\w\s]", " ", s.lower()).split())


def match_score(candidate: Track, raw_title: str, channel: str, target: Track, rank: int) -> float:
    score = -rank * 1.5
    if target.duration and candidate.duration:
        diff = abs(candidate.duration - target.duration)
        score += 30 - diff * 2 if diff <= 15 else -20 - min(diff, 120) / 4
    tw = _words(target.title)
    cw = _words(raw_title)
    if tw:
        score += 40 * len(tw & cw) / len(tw)
    score += 12 * SequenceMatcher(None, target.title.lower(), candidate.title.lower()).ratio()
    main_artist = (target.artist or "").split(",")[0].strip().lower()
    hay = f"{raw_title} {channel}".lower()
    if main_artist and main_artist in hay:
        score += 20
    if channel.endswith("- Topic"):
        score += 12
    if re.search(r"official audio|audio oficial", raw_title, re.I):
        score += 6
    target_text = f"{target.title} {target.album}".lower()
    for bad in _BAD_WORDS:
        if bad in raw_title.lower() and bad not in target_text:
            score -= 25
    return score


def find_match(target: Track) -> Track:
    """Busca en YouTube la mejor versión de una canción (p. ej. de Spotify)."""
    query = target.search_query
    info = _extract(f"ytsearch6:{query}", extract_flat="in_playlist")
    best: tuple[float, Track] | None = None
    for rank, e in enumerate(info.get("entries") or []):
        if not e or not (cand := _entry_to_track(e)):
            continue
        s = match_score(cand, e.get("title") or "", e.get("channel") or "", target, rank)
        if best is None or s > best[0]:
            best = (s, cand)
    if best is None:
        raise YouTubeError(f"No se encontró «{query}» en YouTube")
    return best[1]


# ------------------------------------------------------------------ descargas
ProgressFn = Callable[[float, str], None]


def download_audio(url: str, out_base: str, quality: str, progress: ProgressFn) -> tuple[str, dict]:
    """Descarga el audio y lo convierte a MP3. Devuelve (ruta_mp3, info)."""

    def hook(d: dict[str, Any]) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            progress(min(0.9, 0.9 * done / total) if total else 0.05, "Descargando")
        elif d.get("status") == "finished":
            progress(0.92, "Convirtiendo a MP3")

    opts = base_opts(
        skip_download=False,
        format="bestaudio/best",
        noplaylist=True,
        outtmpl={"default": out_base + ".%(ext)s"},
        overwrites=True,
        progress_hooks=[hook],
        postprocessors=[
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}
        ],
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", str(exc))
        raise YouTubeError(msg.split("\n")[0][:240]) from None
    return out_base + ".mp3", info or {}
