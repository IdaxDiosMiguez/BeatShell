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


def _norm(s: str) -> str:
    import unicodedata

    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower())
                if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", s).strip()


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
                        entry = {"mtime": mtime, **meta}
                        if cached and cached.get("source_key"):
                            entry["source_key"] = cached["source_key"]
                        with self._lock:
                            self._index[full] = entry
                    count += 1
                    if progress and count % 25 == 0:
                        progress(count, fn)
        with self._lock:
            self._index = {k: v for k, v in self._index.items() if k in seen}
        self.save()
        return count

    def add_file(self, path: str, source_key: str | None = None) -> Track:
        p = Path(path)
        meta = read_tags(p)
        with self._lock:
            try:
                entry = {"mtime": os.path.getmtime(p), **meta}
                if source_key:
                    entry["source_key"] = source_key
                self._index[str(p)] = entry
            except OSError:
                pass
        self.save()
        return track_from_file(p, meta)

    def local_for(self, track: Track) -> str | None:
        """¿Tenemos ya esta canción en disco? Por su origen exacto o por artista + título."""
        key = track.key
        title = _norm(track.title)
        artist = _norm((track.artist or "").split(",")[0])
        with self._lock:
            items = list(self._index.items())
        best = None
        for path, meta in items:
            if meta.get("source_key") == key:
                best = path
                break
            if best is None and title and _norm(meta.get("title", "")) == title and artist and \
                    artist in _norm(meta.get("artist", "")):
                best = path
        return best if best and os.path.exists(best) else None

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


# ------------------------------------------------------------------ colección
PLAYLIST, ALBUM, ARTIST, FOLDER = "playlist", "album", "artist", "folder"
KIND_ICON = {PLAYLIST: "☰", ALBUM: "◉", ARTIST: "★", FOLDER: "◫"}
KIND_LABEL = {PLAYLIST: "Playlist", ALBUM: "Álbum", ARTIST: "Artista", FOLDER: "Carpeta"}
_KIND_ORDER = {FOLDER: 0, PLAYLIST: 1, ALBUM: 2, ARTIST: 3}


@dataclass
class Playlist:
    """Un elemento guardado en la biblioteca: playlist, álbum, artista o carpeta."""

    name: str
    tracks: list[Track] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: float = field(default_factory=time.time)
    source_url: str | None = None  # si se importó de YouTube/Spotify
    kind: str = PLAYLIST
    parent: str | None = None  # id de la carpeta que lo contiene
    artist: str = ""  # para álbumes
    thumb: str | None = None

    @property
    def duration(self) -> float:
        return sum(t.duration or 0 for t in self.tracks)

    @property
    def icon(self) -> str:
        return KIND_ICON.get(self.kind, "☰")


class PlaylistStore:
    """Tu colección: playlists, álbumes y artistas guardados, organizados en carpetas."""

    PATH = DATA_DIR / "playlists.json"

    def __init__(self) -> None:
        raw = read_json(self.PATH, [])
        self.items: list[Playlist] = []
        for p in raw:
            try:
                self.items.append(Playlist(
                    name=p["name"], id=p["id"], created=p.get("created", time.time()),
                    source_url=p.get("source_url"), kind=p.get("kind", PLAYLIST),
                    parent=p.get("parent"), artist=p.get("artist", ""), thumb=p.get("thumb"),
                    tracks=[Track.from_dict(t) for t in p.get("tracks", [])],
                ))
            except (KeyError, TypeError):
                continue
        self._lock = threading.Lock()

    def save(self) -> None:
        with self._lock:
            write_json_atomic(self.PATH, [
                {"name": p.name, "id": p.id, "created": p.created, "source_url": p.source_url,
                 "kind": p.kind, "parent": p.parent, "artist": p.artist, "thumb": p.thumb,
                 "tracks": [t.to_dict() for t in p.tracks]}
                for p in self.items
            ])

    # ------------------------------------------------------------- consultas
    def get(self, pid: str | None) -> Playlist | None:
        return next((p for p in self.items if p.id == pid), None)

    @property
    def playlists(self) -> list[Playlist]:
        return [p for p in self.items if p.kind == PLAYLIST]

    @property
    def folders(self) -> list[Playlist]:
        return [p for p in self.items if p.kind == FOLDER]

    def by_name(self, name: str, kind: str = PLAYLIST) -> Playlist | None:
        return next((p for p in self.items
                     if p.kind == kind and p.name.lower() == name.lower()), None)

    def find(self, kind: str, name: str, artist: str = "") -> Playlist | None:
        return next((p for p in self.items if p.kind == kind
                     and p.name.lower() == name.lower()
                     and (not artist or p.artist.lower() == artist.lower())), None)

    def children(self, parent: str | None) -> list[Playlist]:
        items = [p for p in self.items if p.parent == parent]
        return sorted(items, key=lambda p: (_KIND_ORDER.get(p.kind, 9), p.name.lower()))

    def descendants(self, pid: str) -> list[Playlist]:
        out: list[Playlist] = []
        for c in self.children(pid):
            out.append(c)
            if c.kind == FOLDER:
                out.extend(self.descendants(c.id))
        return out

    def folder_tracks(self, pid: str) -> list[Track]:
        return [t for c in self.descendants(pid) if c.kind != FOLDER for t in c.tracks]

    def path_of(self, pid: str | None) -> str:
        names = []
        p = self.get(pid)
        while p is not None:
            names.append(p.name)
            p = self.get(p.parent)
        return " / ".join(reversed(names))

    def unique_name(self, base: str, kind: str = PLAYLIST) -> str:
        name, n = base, 2
        while self.by_name(name, kind):
            name, n = f"{base} ({n})", n + 1
        return name

    # ------------------------------------------------------------- cambios
    def create(self, name: str, tracks: Iterable[Track] = (), source_url: str | None = None,
               kind: str = PLAYLIST, parent: str | None = None, artist: str = "",
               thumb: str | None = None) -> Playlist:
        default = {FOLDER: "Nueva carpeta", PLAYLIST: "Nueva playlist"}.get(kind, "Sin nombre")
        p = Playlist(name=self.unique_name(name.strip() or default, kind),
                     tracks=[t.copy() for t in tracks], source_url=source_url, kind=kind,
                     parent=parent if self.get(parent) else None, artist=artist, thumb=thumb)
        self.items.append(p)
        self.save()
        return p

    def delete(self, pid: str) -> None:
        """Borra un elemento. Si es una carpeta, su contenido sube un nivel (no se pierde)."""
        p = self.get(pid)
        if not p:
            return
        for c in self.children(pid):
            c.parent = p.parent
        self.items = [x for x in self.items if x.id != pid]
        self.save()

    def rename(self, pid: str, name: str) -> None:
        if (p := self.get(pid)) and name.strip():
            p.name = name.strip()
            self.save()

    def move_to(self, pid: str, folder_id: str | None) -> bool:
        p = self.get(pid)
        if not p:
            return False
        if folder_id is not None:
            target = self.get(folder_id)
            if target is None or target.kind != FOLDER or folder_id == pid:
                return False
            if p.kind == FOLDER and any(d.id == folder_id for d in self.descendants(pid)):
                return False  # no se puede meter una carpeta dentro de sí misma
        p.parent = folder_id
        self.save()
        return True

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
        """Tras descargar una canción, los elementos que la contienen pasan a usar el MP3."""
        changed = False
        for p in self.items:
            for t in p.tracks:
                if t.key == key and t.path != path:
                    t.path = path
                    changed = True
        if changed:
            self.save()


# ------------------------------------------------------------------ M3U8
def export_m3u(playlist: Playlist, path: str | Path) -> Path:
    """Exporta a M3U8 (lo abren VLC, foobar, Winamp, Music…).

    Las canciones locales se guardan con su ruta; las de internet, con su enlace.
    """
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist.name}"]
    for t in playlist.tracks:
        if t.path and os.path.exists(t.path):
            target = t.path
        elif t.url:
            target = t.url
        elif t.spotify_uri:
            target = "https://open.spotify.com/track/" + t.spotify_uri.rsplit(":", 1)[-1]
        else:
            continue
        dur = int(t.duration) if t.duration else -1
        label = f"{t.artist} - {t.title}" if t.artist else t.title
        lines.append(f"#EXTINF:{dur},{label}")
        if t.album:
            lines.append(f"#EXTALB:{t.album}")
        lines.append(target)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def import_m3u(path: str | Path) -> tuple[str, list[Track], int]:
    """Lee un M3U/M3U8. Devuelve (nombre, canciones, entradas que no se encontraron)."""
    from .models import SPOTIFY, YOUTUBE

    src = Path(path).expanduser()
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    name = src.stem
    tracks: list[Track] = []
    missing = 0
    info: tuple[float | None, str] | None = None
    album = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#PLAYLIST:"):
            name = line[10:].strip() or name
            continue
        if line.startswith("#EXTINF:"):
            head, _, label = line[8:].partition(",")
            try:
                dur = float(head.split()[0])
            except (ValueError, IndexError):
                dur = None
            info = (dur if dur and dur > 0 else None, label.strip())
            continue
        if line.startswith("#EXTALB:"):
            album = line[8:].strip()
            continue
        if line.startswith("#"):
            continue
        dur, label = info or (None, "")
        artist, title = "", label
        if " - " in label:
            artist, title = (x.strip() for x in label.split(" - ", 1))
        info = None
        if line.startswith(("http://", "https://")):
            m = re.search(r"open\.spotify\.com/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]+)", line)
            if m:
                tracks.append(Track(title=title or "Canción de Spotify", artist=artist, album=album,
                                    duration=dur, source=SPOTIFY,
                                    spotify_uri=f"spotify:track:{m.group(1)}"))
            else:
                tracks.append(Track(title=title or line, artist=artist, album=album,
                                    duration=dur, source=YOUTUBE, url=line))
        else:
            p = Path(line.replace("file://", ""))
            if not p.is_absolute():
                p = (src.parent / p).resolve()
            if p.exists():
                tracks.append(track_from_file(p))
            else:
                missing += 1
        album = ""
    return name, tracks, missing


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
