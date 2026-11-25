"""Puente entre la app Android (Kotlin) y el núcleo de BeatShell (src/BeatShell).

Reutiliza tal cual la búsqueda, las playlists de YouTube, la lectura de Spotify y el
emparejado de canciones del núcleo. Lo único propio de Android es cómo se saca el stream
de audio: sin ffmpeg ni motor de JavaScript, con clientes de YouTube que no los necesitan.

Todas las funciones públicas devuelven JSON (texto) para que Kotlin no dependa de objetos
de Python. Los errores se lanzan como excepciones con un mensaje legible en español.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import types
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

_YTDLP_DIR: str | None = None

_NETWORK = re.compile(
    r"Errno|TransportError|timed out|Network is unreachable|Connection (refused|reset|aborted)|"
    r"Temporary failure|getaddrinfo|No address associated|Name or service not known|"
    r"No se pudo conectar|Remote end closed|SSLError|EOF occurred", re.I)


def _network(fn):
    """Los fallos de red salen como ConnectionError (Kotlin los distingue: no salta canciones)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _NETWORK.search(str(exc)):
                raise ConnectionError("Sin conexión: revisa tu red") from None
            raise

    return wrapper


# --------------------------------------------------------------------- arranque
def _install_stubs() -> None:
    """El núcleo importa módulos de escritorio (audio con sounddevice, ffmpeg) que en Android
    no existen ni hacen falta: se sustituyen por equivalentes mínimos antes de importarlo."""
    if "BeatShell.audio" not in sys.modules:
        audio = types.ModuleType("BeatShell.audio")

        @dataclass
        class Source:
            input: str
            headers: dict = field(default_factory=dict)
            duration: float | None = None
            available_at: float = 0.0
            fast: bool = False

        audio.Source = Source
        sys.modules["BeatShell.audio"] = audio
    if "BeatShell.ffmpeg" not in sys.modules:
        ff = types.ModuleType("BeatShell.ffmpeg")

        def ffmpeg_exe() -> str:
            raise RuntimeError("ffmpeg no está disponible en Android")

        ff.ffmpeg_exe = ffmpeg_exe
        ff.POPEN_FLAGS = {}
        sys.modules["BeatShell.ffmpeg"] = ff


def init(files_dir: str) -> str:
    """Prepara rutas (yt-dlp actualizado desde la app, si lo hay) y precarga el núcleo."""
    global _YTDLP_DIR
    _YTDLP_DIR = os.path.join(files_dir, "yt-dlp")
    wheel = os.path.join(_YTDLP_DIR, "yt_dlp.whl")
    if os.path.exists(wheel) and wheel not in sys.path:
        sys.path.insert(0, wheel)
    _no_subprocesses()
    _install_stubs()
    from BeatShell.sources import spotify, youtube  # noqa: F401  (precarga)

    # En Android no hay deno/node/bun: sin esto yt-dlp intenta ejecutarlos para ver si existen.
    youtube._js_runtimes = lambda: {}
    return ytdlp_version()


def _no_subprocesses() -> None:
    """Hacer fork() dentro del proceso de una app Android es peligroso y aquí no hay binarios
    que lanzar (ni ffmpeg ni motores JS): cualquier intento falla al momento como "no existe"."""
    import subprocess

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("No se pueden lanzar programas en Android")

    subprocess.Popen.__init__ = refuse  # type: ignore[method-assign]
    subprocess.Popen._child_created = False  # para que __del__ no se queje
    os.fork = refuse  # type: ignore[assignment]


def _yt():
    _install_stubs()
    from BeatShell.sources import youtube

    return youtube


def ytdlp_version() -> str:
    import yt_dlp

    return yt_dlp.version.__version__


# --------------------------------------------------------------------- pistas
def _track(t: Any) -> dict:
    return t.to_dict()


def _collection(col: Any) -> dict:
    return {
        "name": col.name,
        "kind": col.kind,
        "source": col.source,
        "owner": col.owner,
        "thumb": col.thumb,
        "url": col.url,
        "tracks": [_track(t) for t in col.tracks],
    }


@_network
def search(query: str, limit: int = 25) -> str:
    yt = _yt()
    tracks = yt.search(query, limit)
    return json.dumps({"name": query, "kind": "search", "source": "youtube", "owner": "",
                       "thumb": None, "url": None, "tracks": [_track(t) for t in tracks]})


_URL_RE = re.compile(r"(https?://\S+|spotify:(?:track|album|playlist):[A-Za-z0-9]+)")


def extract_link(text: str) -> str | None:
    """Saca el primer enlace de un texto compartido ("Escucha esto: https://…")."""
    m = _URL_RE.search(text or "")
    return m.group(1).rstrip(").,;»\"'") if m else None


@_network
def resolve(text: str) -> str:
    """Lo que escribió o compartió el usuario → colección (búsqueda, vídeo, playlist, Spotify)."""
    _install_stubs()
    from BeatShell.sources import classify, spotify

    link = extract_link(text)
    kind, value = classify(link or text)
    yt = _yt()
    if kind == "spotify":
        col = spotify.fetch(value)
        if not col.tracks:
            raise RuntimeError("Ese enlace de Spotify no tiene canciones disponibles")
        return json.dumps(_collection(col))
    if kind == "youtube_playlist":
        col = yt.playlist(value)
        if not col.tracks:
            raise RuntimeError("La playlist está vacía o es privada")
        return json.dumps(_collection(col))
    if kind == "youtube_video":
        t = video_track(value)
        return json.dumps({"name": t["title"], "kind": "video", "source": "youtube",
                           "owner": t.get("artist", ""), "thumb": t.get("thumb"),
                           "url": t.get("url"), "tracks": [t]})
    if kind == "url":
        raise RuntimeError("Ese enlace no es de YouTube ni de Spotify")
    return search(value)


# --------------------------------------------------------------------- streams
# Comprobado con yt-dlp 2026.11.23 y sin motor de JavaScript:
# - android_music / ios_music: audio puro m4a (AAC 128k) u opus, pero YouTube obliga a
#   esperar ~5 s ("anuncio" previo) antes de servirlo: antes de esa hora responde 403.
# - android: solo el formato 18 (mp4 360p con AAC 96k), pero sin espera. Se usa para que la
#   canción que el usuario pulsa empiece al momento; las siguientes de la cola se resuelven
#   con antelación en alta calidad y la espera no se nota.
HQ_CLIENTS = ["android_music", "ios_music"]
HQ_FORMAT = "140/bestaudio[ext=m4a]/251/bestaudio"
FAST_CLIENTS = ["android"]
FAST_FORMAT = "18/bestaudio/best[height<=480]"


def _video_url(video: str) -> str:
    return video if video.startswith("http") else f"https://www.youtube.com/watch?v={video}"


def _expire(url: str) -> float:
    try:
        return float(parse_qs(urlparse(url).query)["expire"][0])
    except (KeyError, ValueError, IndexError):
        return time.time() + 3 * 3600


def _extract_stream(video: str, fast: bool = False) -> dict:
    yt = _yt()
    url = _video_url(video)
    plan = [(c, FAST_FORMAT) for c in FAST_CLIENTS] if fast else []
    plan += [(c, HQ_FORMAT) for c in HQ_CLIENTS]
    if not fast:
        plan += [(c, FAST_FORMAT) for c in FAST_CLIENTS]  # último recurso
    last: Exception | None = None
    for client, fmt in plan:
        try:
            info = yt._extract(url, format=fmt, noplaylist=True,
                               extractor_args={"youtube": {"player_client": [client]}})
        except yt.YouTubeError as exc:
            last = exc
            continue
        stream = info.get("url")
        if not stream and info.get("requested_formats"):
            stream = info["requested_formats"][0].get("url")
        if not stream:
            continue
        info["_client"] = client
        info["_stream"] = stream
        return info
    raise RuntimeError(str(last) if last else "No hay un stream de audio para este vídeo")


@_network
def stream(video: str, fast: bool = False) -> str:
    """URL directa del audio, cabeceras que hay que mandar y desde cuándo se puede pedir."""
    info = _extract_stream(video, fast)
    yt = _yt()
    artist, title = yt.split_artist_title(info.get("title") or "", info.get("channel") or
                                         info.get("uploader") or "")
    return json.dumps({
        "url": info["_stream"],
        "headers": dict(info.get("http_headers") or {}),
        "expire": _expire(info["_stream"]),
        "available_at": float(info.get("available_at") or 0),
        "client": info["_client"],
        "format": info.get("format_id"),
        "duration": info.get("duration"),
        "id": info.get("id"),
        "title": title,
        "artist": artist,
    })


def video_track(url: str) -> dict:
    info = _extract_stream(url, fast=True)
    yt = _yt()
    artist, title = yt.split_artist_title(info.get("title") or url, info.get("channel") or
                                         info.get("uploader") or "")
    vid = info.get("id")
    from BeatShell.models import YOUTUBE, Track

    return Track(
        title=title, artist=artist, source=YOUTUBE,
        duration=float(info["duration"]) if info.get("duration") else None,
        url=f"https://www.youtube.com/watch?v={vid}" if vid else url,
        thumb=yt.thumb_for(vid),
    ).to_dict()


@_network
def match(track_json: str) -> str:
    """Busca en YouTube la mejor versión de una canción (p. ej. de Spotify)."""
    _install_stubs()
    from BeatShell.models import Track

    found = _yt().find_match(Track.from_dict(json.loads(track_json)))
    return json.dumps(found.to_dict())


# --------------------------------------------------------------------- descargas
@_network
def download(video: str, out_base: str, meta_json: str, progress: Any) -> str:
    """Descarga el audio en M4A (sin recodificar: no hay ffmpeg) y le pone etiquetas.

    `progress` es un objeto Java con `update(float, String)`.
    """
    import yt_dlp

    yt = _yt()
    meta = json.loads(meta_json)

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            progress.update(0.95 * done / total if total else 0.0, "Descargando")
        elif d.get("status") == "finished":
            progress.update(0.96, "Guardando")

    info: dict | None = None
    last: Exception | None = None
    for client in HQ_CLIENTS:
        opts = yt.base_opts(
            skip_download=False, format="140/bestaudio[ext=m4a]", noplaylist=True,
            outtmpl={"default": out_base + ".%(ext)s"}, overwrites=True,
            progress_hooks=[hook], fixup="never", http_chunk_size=10 * 1024 * 1024,
            extractor_args={"youtube": {"player_client": [client]}},
        )
        opts.pop("ffmpeg_location", None)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(_video_url(video), download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            last = exc
    if not info:
        msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", str(last or "Error desconocido"))
        raise RuntimeError(msg.split("\n")[0][:240])
    path = out_base + "." + (info.get("ext") or "m4a")
    try:
        _tag_m4a(path, meta, info)
    except Exception:  # noqa: BLE001 - las etiquetas son un extra; el audio ya está
        pass
    return path


def _tag_m4a(path: str, meta: dict, info: dict) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    yt = _yt()
    artist, title = yt.split_artist_title(info.get("title") or "", info.get("channel") or "")
    audio = MP4(path)
    audio["\xa9nam"] = [meta.get("title") or title]
    audio["\xa9ART"] = [meta.get("artist") or artist]
    if meta.get("album"):
        audio["\xa9alb"] = [meta["album"]]
    cover = meta.get("cover") or (f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                                  if info.get("id") else None)
    if cover:
        req = urllib.request.Request(cover, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        fmt = MP4Cover.FORMAT_PNG if data[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(data, imageformat=fmt)]
    audio.save()


# --------------------------------------------------------------------- actualizar yt-dlp
@_network
def update_ytdlp() -> str:
    """Descarga de PyPI la última versión de yt-dlp (se usa en el próximo arranque).

    YouTube cambia a menudo; así la app puede ponerse al día sin publicar otro APK.
    """
    import hashlib

    if not _YTDLP_DIR:
        raise RuntimeError("Python no se ha inicializado")
    req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json",
                                 headers={"User-Agent": "BeatShell-Android"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    latest = data["info"]["version"]
    if latest == ytdlp_version():
        return json.dumps({"version": latest, "updated": False})
    req_py = data["info"].get("requires_python") or ""
    m = re.search(r">=\s*3\.(\d+)", req_py)
    if m and sys.version_info[1] < int(m.group(1)):
        raise RuntimeError(f"yt-dlp {latest} necesita Python {req_py}; actualiza la app")
    wheel = next((f for f in data["urls"]
                  if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("py3-none-any.whl")),
                 None)
    if wheel is None:
        raise RuntimeError("No hay un paquete de yt-dlp compatible")
    req = urllib.request.Request(wheel["url"], headers={"User-Agent": "BeatShell-Android"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        blob = resp.read()
    if hashlib.sha256(blob).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError("La descarga de yt-dlp está corrupta")
    os.makedirs(_YTDLP_DIR, exist_ok=True)
    tmp = os.path.join(_YTDLP_DIR, "yt_dlp.whl.tmp")
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, os.path.join(_YTDLP_DIR, "yt_dlp.whl"))
    return json.dumps({"version": latest, "updated": True})


def reset_ytdlp() -> None:
    """Vuelve al yt-dlp que viene con la app (por si una actualización falla)."""
    if _YTDLP_DIR:
        try:
            os.remove(os.path.join(_YTDLP_DIR, "yt_dlp.whl"))
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------- prueba rápida
def selftest() -> str:
    """Comprueba sin red que el núcleo se importa y que los enlaces se clasifican bien."""
    _install_stubs()
    from BeatShell.sources import classify

    cases = {
        "https://youtu.be/dQw4w9WgXcQ": "youtube_video",
        "https://www.youtube.com/playlist?list=PL123": "youtube_playlist",
        "https://open.spotify.com/intl-es/album/4aawyAB9vmqN3uQ7FjRGTy?si=x": "spotify",
        "Mira esto https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC": "spotify",
        "bad bunny": "search",
    }
    for text, want in cases.items():
        got = classify(extract_link(text) or text)[0]
        if got != want:
            raise AssertionError(f"{text!r}: {got} != {want}")
    _yt()
    return "ok " + ytdlp_version()
