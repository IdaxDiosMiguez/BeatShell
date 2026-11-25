"""Bibliotecas locales (carpetas escaneadas), playlists y estado persistente."""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import DATA_DIR, read_json, write_json_atomic
from .models import LOCAL, Track

AUDIO_EXTS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma",
    ".aiff", ".aif", ".alac", ".ape", ".wv", ".mka", ".webm",
}


# ------------------------------------------------------------------ metadatos
def _first(tags, *keys: str) -> str:
    if not tags:
        return ""
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if v:
            v = v[0] if isinstance(v, list) else v
            return str(v).strip()
    return ""


def read_tags(path: Path) -> dict:
    title = artist = album = ""
    duration = None
    track_no = None
    try:
        import mutagen

        f = mutagen.File(str(path), easy=True)
        if f is not None:
            duration = float(getattr(f.info, "length", 0) or 0) or None
            title = _first(f.tags, "title")
            artist = _first(f.tags, "artist", "albumartist")
            album = _first(f.tags, "album")
            tn = _first(f.tags, "tracknumber")
            if tn:
                m = re.match(r"\d+", tn)
                track_no = int(m.group()) if m else None
    except Exception:
        pass
    if not title:
        stem = path.stem
        stem = re.sub(r"^\d{1,3}[\s._-]+", "", stem)  # "01 - Canción"
        if " - " in stem and not artist:
            artist, title = (s.strip() for s in stem.split(" - ", 1))
        else:
            title = stem
    if not album and path.parent.name:
        album = path.parent.name
    return {"title": title, "artist": artist, "album": album,
            "duration": duration, "track_no": track_no}


def track_from_file(path: Path, meta: dict | None = None) -> Track:
    meta = meta or read_tags(path)
    return Track(source=LOCAL, path=str(path), **{k: meta.get(k) for k in
                 ("title", "artist", "album", "duration", "track_no")})


# ------------------------------------------------------------------ biblioteca
@dataclass
class Folder:
    name: str
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class Library:
    """Varias bibliotecas (carpetas) con un índice cacheado de metadatos."""

    PATH = DATA_DIR / "library.json"

    def __init__(self) -> None:
        raw = read_json(self.PATH, {})
        self.folders: list[Folder] = [Folder(**f) for f in raw.get("folders", [])]
        # ruta → {mtime, meta}
        self._index: dict[str, dict] = raw.get("index", {})
        self._lock = threading.RLock()

    def save(self) -> None:
        with self._lock:
            write_json_atomic(self.PATH, {
                "folders": [f.__dict__ for f in self.folders],
                "index": self._index,
            })

    # ------------------------------------------------------------- carpetas
    def add_folder(self, path: str, name: str | None = None) -> Folder:
        p = str(Path(path).expanduser().resolve())
        for f in self.folders:
            if os.path.normcase(f.path) == os.path.normcase(p):
                return f
        folder = Folder(name=name or Path(p).name or p, path=p)
        self.folders.append(folder)
        self.save()
        return folder

    def remove_folder(self, folder_id: str) -> None:
        with self._lock:
            gone = [f for f in self.folders if f.id == folder_id]
            self.folders = [f for f in self.folders if f.id != folder_id]
            for f in gone:
                prefix = os.path.normcase(f.path)
                self._index = {k: v for k, v in self._index.items()
                               if not os.path.normcase(k).startswith(prefix)}
        self.save()

    def rename_folder(self, folder_id: str, name: str) -> None:
        for f in self.folders:
            if f.id == folder_id:
                f.name = name
        self.save()

    def folder(self, folder_id: str) -> Folder | None:
        return next((f for f in self.folders if f.id == folder_id), None)

    # --------------------------------------------------------------- escaneo
    def scan(self, progress: Callable[[int, str], None] | None = None) -> int:
        """Re-escanea todas las carpetas. Solo relee archivos modificados."""
        seen: set[str] = set()
        count = 0
        for folder in list(self.folders):
            root = Path(folder.path)
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if fn.startswith(".") or Path(fn).suffix.lower() not in AUDIO_EXTS:
                        continue
                    full = os.path.join(dirpath, fn)
                    seen.add(full)
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        continue
                    cached = self._index.get(full)
                    if not cached or cached.get("mtime") != mtime:
                        meta = read_tags(Path(full))
                        with self._lock:
                            self._index[full] = {"mtime": mtime, **meta}
                    count += 1
                    if progress and count % 25 == 0:
                        progress(count, fn)
        with self._lock:
            self._index = {k: v for k, v in self._index.items() if k in seen}
        self.save()
        return count

    def add_file(self, path: str) -> Track:
        p = Path(path)
        meta = read_tags(p)
        with self._lock:
            try:
                self._index[str(p)] = {"mtime": os.path.getmtime(p), **meta}
            except OSError:
                pass
        self.save()
        return track_from_file(p, meta)

    # --------------------------------------------------------------- consulta
    def tracks(self, folder_id: str | None = None) -> list[Track]:
        with self._lock:
            items = list(self._index.items())
        if folder_id:
            f = self.folder(folder_id)
            if not f:
                return []
            prefix = os.path.normcase(os.path.join(f.path, ""))
            items = [(k, v) for k, v in items if os.path.normcase(k).startswith(prefix)]
        tracks = [track_from_file(Path(k), v) for k, v in items]
        tracks.sort(key=lambda t: ((t.artist or "~").lower(), (t.album or "").lower(),
                                   t.track_no or 0, t.title.lower()))
        return tracks

    def count(self, folder_id: str | None = None) -> int:
        if folder_id is None:
            return len(self._index)
        return len(self.tracks(folder_id))


# ------------------------------------------------------------------ playlists
@dataclass
class Playlist:
    name: str
    tracks: list[Track] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: float = field(default_factory=time.time)
    source_url: str | None = None  # si se importó de YouTube/Spotify

    @property
    def duration(self) -> float:
        return sum(t.duration or 0 for t in self.tracks)


class PlaylistStore:
    PATH = DATA_DIR / "playlists.json"

    def __init__(self) -> None:
        raw = read_json(self.PATH, [])
        self.items: list[Playlist] = []
        for p in raw:
            try:
                self.items.append(Playlist(
                    name=p["name"], id=p["id"], created=p.get("created", time.time()),
                    source_url=p.get("source_url"),
                    tracks=[Track.from_dict(t) for t in p.get("tracks", [])],
                ))
            except (KeyError, TypeError):
                continue
        self._lock = threading.Lock()

    def save(self) -> None:
        with self._lock:
            write_json_atomic(self.PATH, [
                {"name": p.name, "id": p.id, "created": p.created, "source_url": p.source_url,
                 "tracks": [t.to_dict() for t in p.tracks]}
                for p in self.items
            ])

    def get(self, pid: str) -> Playlist | None:
        return next((p for p in self.items if p.id == pid), None)

    def by_name(self, name: str) -> Playlist | None:
        return next((p for p in self.items if p.name.lower() == name.lower()), None)

    def unique_name(self, base: str) -> str:
        name, n = base, 2
        while self.by_name(name):
            name, n = f"{base} ({n})", n + 1
        return name

    def create(self, name: str, tracks: Iterable[Track] = (), source_url: str | None = None) -> Playlist:
        p = Playlist(name=self.unique_name(name.strip() or "Nueva playlist"),
                     tracks=[t.copy() for t in tracks], source_url=source_url)
        self.items.append(p)
        self.save()
        return p

    def delete(self, pid: str) -> None:
        self.items = [p for p in self.items if p.id != pid]
        self.save()

    def rename(self, pid: str, name: str) -> None:
        if (p := self.get(pid)) and name.strip():
            p.name = name.strip()
            self.save()

    def add(self, pid: str, tracks: Iterable[Track]) -> int:
        p = self.get(pid)
        if not p:
            return 0
        new = [t.copy() for t in tracks]
        p.tracks.extend(new)
        self.save()
        return len(new)

    def remove(self, pid: str, uid: str) -> None:
        if p := self.get(pid):
            p.tracks = [t for t in p.tracks if t.uid != uid]
            self.save()

    def move(self, pid: str, index: int, delta: int) -> int:
        p = self.get(pid)
        if not p or not (0 <= index < len(p.tracks)):
            return index
        j = max(0, min(len(p.tracks) - 1, index + delta))
        p.tracks.insert(j, p.tracks.pop(index))
        self.save()
        return j

    def link_local(self, key: str, path: str) -> None:
        """Tras descargar una canción, las playlists que la contienen pasan a usar el MP3."""
        changed = False
        for p in self.items:
            for t in p.tracks:
                if t.key == key and t.path != path:
                    t.path = path
                    changed = True
        if changed:
            self.save()


# ------------------------------------------------------------------ cachés
class MatchCache:
    """Recuerda qué vídeo de YouTube corresponde a cada canción de Spotify."""

    PATH = DATA_DIR / "matches.json"

    def __init__(self) -> None:
        self._d: dict[str, dict] = read_json(self.PATH, {})
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        return self._d.get(key)

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._d[key] = value
            write_json_atomic(self.PATH, self._d)


SESSION_PATH = DATA_DIR / "session.json"


def save_session(tracks: list[Track], index: int) -> None:
    write_json_atomic(SESSION_PATH, {"index": index, "tracks": [t.to_dict() for t in tracks]})


def load_session() -> tuple[list[Track], int]:
    raw = read_json(SESSION_PATH, {})
    tracks = []
    for t in raw.get("tracks", []):
        try:
            tracks.append(Track.from_dict(t))
        except TypeError:
            continue
    return tracks, int(raw.get("index", -1))
