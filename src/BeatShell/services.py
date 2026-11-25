"""Servicios que combinan fuentes y almacenamiento: resolver pistas y descargar MP3."""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import threading
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio import Source
from .config import Config
from .library import Library, MatchCache
from .models import SPOTIFY, Track
from .sources import youtube

# ------------------------------------------------------------------ resolver


class Resolver:
    """Convierte cualquier Track en algo reproducible por el motor de audio."""

    def __init__(self, matches: MatchCache) -> None:
        self.matches = matches
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def ensure_url(self, track: Track) -> str:
        """Para pistas de Spotify, encuentra (y recuerda) su vídeo en YouTube."""
        if track.url:
            return track.url
        key = track.key
        with self._lock_for(key):
            hit = self.matches.get(key)
            if hit:
                track.url = hit["url"]
                track.thumb = track.thumb or hit.get("thumb")
                return track.url
            m = youtube.find_match(track)
            track.url = m.url
            track.thumb = track.thumb or m.thumb
            if not track.duration:
                track.duration = m.duration
            self.matches.put(key, {"url": m.url, "thumb": m.thumb})
            return track.url

    def resolve(self, track: Track) -> Source:
        if track.path and os.path.exists(track.path):
            return Source(input=track.path, duration=track.duration)
        url = self.ensure_url(track)
        src, meta = youtube.stream_info(url)
        if not track.duration and src.duration:
            track.duration = src.duration
        if meta.get("is_live"):
            src.duration = None
        elif track.duration and not src.duration:
            src.duration = track.duration
        return src

    def prefetch(self, track: Track | None) -> None:
        if track is None or (track.path and os.path.exists(track.path)):
            return

        def run() -> None:
            try:
                self.resolve(track)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True, name="prefetch").start()


# ------------------------------------------------------------------ archivos
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str, limit: int = 120) -> str:
    """Nombre de archivo válido en Windows, macOS y Linux."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    if s.upper() in _WIN_RESERVED:
        s = f"_{s}"
    return (s[:limit].rstrip(". ") or "Sin título")


def fetch_bytes(url: str, timeout: float = 15) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def square_jpeg(data: bytes, size: int = 600) -> bytes | None:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        s = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
        img = img.resize((min(size, s), min(size, s)), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return None


def write_id3(path: str, track: Track, cover: bytes | None) -> None:
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("APIC")
    tags.setall("TIT2", [TIT2(encoding=3, text=track.title)])
    if track.artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=track.artist)])
        tags.setall("TPE2", [TPE2(encoding=3, text=track.artist.split(",")[0].strip())])
    if track.album:
        tags.setall("TALB", [TALB(encoding=3, text=track.album)])
    if track.track_no:
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track.track_no))])
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)


# ------------------------------------------------------------------ descargas
QUEUED, WORKING, DONE, SKIPPED, FAILED = "en cola", "trabajando", "listo", "ya existía", "error"


@dataclass
class DownloadJob:
    track: Track
    dest_dir: str
    group: str  # nombre de la playlist/álbum o "Canciones"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = QUEUED
    stage: str = "En cola"
    progress: float = 0.0
    error: str = ""
    path: str = ""
    key: str = ""  # identidad original de la canción (antes de pasar a ser local)

    @property
    def finished(self) -> bool:
        return self.status in (DONE, SKIPPED, FAILED)


class Downloader:
    def __init__(self, cfg: Config, resolver: Resolver, library: Library,
                 on_change: Callable[[DownloadJob], None]) -> None:
        self.cfg = cfg
        self.resolver = resolver
        self.library = library
        self.on_change = on_change
        self.jobs: list[DownloadJob] = []
        self._pool = ThreadPoolExecutor(max_workers=max(1, cfg.concurrent_downloads),
                                        thread_name_prefix="download")
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return Path(self.cfg.download_dir).expanduser()

    def enqueue(self, tracks: list[Track], group: str | None = None) -> list[DownloadJob]:
        group = safe_name(group or "Canciones")
        dest = str(self.root / group)
        new = []
        with self._lock:
            pending = {(j.track.key, j.dest_dir) for j in self.jobs if not j.finished}
            for t in tracks:
                if (t.key, dest) in pending:
                    continue
                job = DownloadJob(track=t.copy(), dest_dir=dest, group=group, key=t.key)
                job.track.uid = t.uid  # mismo uid para poder enlazar después
                self.jobs.append(job)
                new.append(job)
        for job in new:
            self.on_change(job)
            self._pool.submit(self._run, job)
        return new

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if not j.finished]

    def retry_failed(self) -> int:
        failed = [j for j in self.jobs if j.status == FAILED]
        for j in failed:
            j.status, j.stage, j.progress, j.error = QUEUED, "En cola", 0.0, ""
            self.on_change(j)
            self._pool.submit(self._run, j)
        return len(failed)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _set(self, job: DownloadJob, **kw) -> None:
        for k, v in kw.items():
            setattr(job, k, v)
        self.on_change(job)

    def _run(self, job: DownloadJob) -> None:
        t = job.track
        try:
            self._set(job, status=WORKING, stage="Buscando", progress=0.02)
            # Si ya es un archivo local no hay nada que descargar.
            if t.path and os.path.exists(t.path):
                self._set(job, status=SKIPPED, stage="Ya es local", progress=1.0, path=t.path)
                return
            url = self.resolver.ensure_url(t)
            if t.source != SPOTIFY or not t.artist:
                # Para YouTube intentamos completar artista/título desde el vídeo.
                if not t.artist:
                    t.artist = youtube.video_track(url).artist
            fname = safe_name(f"{t.artist} - {t.title}" if t.artist else t.title)
            dest_dir = Path(job.dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = dest_dir / f"{fname}.mp3"
            if final.exists():
                self._finish(job, str(final), SKIPPED)
                return

            with tempfile.TemporaryDirectory(prefix="beatshell-") as tmp:
                base = os.path.join(tmp, "audio")
                mp3, info = youtube.download_audio(
                    url, base, self.cfg.mp3_quality,
                    lambda p, stage: self._set(job, progress=p, stage=stage),
                )
                if not os.path.exists(mp3):
                    raise RuntimeError("La conversión a MP3 no produjo archivo")
                self._set(job, stage="Etiquetando", progress=0.96)
                cover = None
                for url_img in (t.thumb if t.source == SPOTIFY else None, info.get("thumbnail"), t.thumb):
                    if url_img and (raw := fetch_bytes(url_img)) and (cover := square_jpeg(raw)):
                        break
                if not t.duration and info.get("duration"):
                    t.duration = float(info["duration"])
                write_id3(mp3, t, cover)
                shutil.move(mp3, final)
            self._finish(job, str(final), DONE)
        except Exception as exc:  # noqa: BLE001 - mostramos cualquier error al usuario
            self._set(job, status=FAILED, stage="Error", error=str(exc)[:200] or type(exc).__name__)

    def _finish(self, job: DownloadJob, path: str, status: str) -> None:
        self.library.add_file(path)
        job.track.path = path
        self._set(job, status=status, stage="Listo" if status == DONE else "Ya existía",
                  progress=1.0, path=path)
