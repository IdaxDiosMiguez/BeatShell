//! Bibliotecas locales (carpetas escaneadas), playlists y sesión.
//!
//! Lee y escribe `library.json`, `playlists.json` y `session.json` con el mismo
//! formato que la versión Python.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::config::{data_path, read_json, write_json_atomic};
use crate::models::{LOCAL, Track, new_uid};

pub const AUDIO_EXTS: &[&str] = &[
    "mp3", "flac", "m4a", "aac", "ogg", "oga", "opus", "wav", "wma", "aiff", "aif", "alac", "ape",
    "wv", "mka", "webm",
];

pub fn is_audio(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| AUDIO_EXTS.contains(&e.to_lowercase().as_str()))
}

pub fn is_playlist_file(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| matches!(e.to_lowercase().as_str(), "m3u" | "m3u8"))
}

// ------------------------------------------------------------------ metadatos
#[derive(Debug, Clone, Default)]
pub struct Tags {
    pub title: String,
    pub artist: String,
    pub album: String,
    pub duration: Option<f64>,
    pub track_no: Option<i64>,
}

pub fn read_tags(path: &Path) -> Tags {
    use lofty::prelude::*;

    let mut t = Tags::default();
    if let Ok(f) = lofty::read_from_path(path) {
        let secs = f.properties().duration().as_secs_f64();
        t.duration = (secs > 0.0).then_some(secs);
        if let Some(tag) = f.primary_tag().or_else(|| f.first_tag()) {
            t.title = tag
                .title()
                .map(|s| s.trim().to_string())
                .unwrap_or_default();
            t.artist = tag
                .artist()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    tag.get_string(ItemKey::AlbumArtist)
                        .map(|s| s.trim().to_string())
                })
                .unwrap_or_default();
            t.album = tag
                .album()
                .map(|s| s.trim().to_string())
                .unwrap_or_default();
            t.track_no = tag.track().map(i64::from);
        }
    }
    if t.title.is_empty() {
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let stem = strip_track_prefix(&stem);
        match stem.split_once(" - ") {
            Some((a, b)) if t.artist.is_empty() => {
                t.artist = a.trim().to_string();
                t.title = b.trim().to_string();
            }
            _ => t.title = stem.to_string(),
        }
    }
    if t.album.is_empty()
        && let Some(name) = path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
    {
        t.album = name.to_string();
    }
    t
}

/// Quita "01 - " / "01. " del principio (regex de Python `^\d{1,3}[\s._-]+`).
fn strip_track_prefix(s: &str) -> &str {
    let digits = s.chars().take_while(|c| c.is_ascii_digit()).count();
    if !(1..=3).contains(&digits) {
        return s;
    }
    let rest = &s[digits..];
    let sep = rest
        .chars()
        .take_while(|c| c.is_whitespace() || matches!(c, '.' | '_' | '-'))
        .map(char::len_utf8)
        .sum::<usize>();
    if sep == 0 { s } else { &rest[sep..] }
}

pub fn track_from_meta(path: &str, meta: &Map<String, Value>) -> Track {
    let mut t = Track::new(meta.get("title").and_then(Value::as_str).unwrap_or(""));
    t.source = LOCAL.into();
    t.path = Some(path.to_string());
    t.artist = meta
        .get("artist")
        .and_then(Value::as_str)
        .unwrap_or("")
        .into();
    t.album = meta
        .get("album")
        .and_then(Value::as_str)
        .unwrap_or("")
        .into();
    t.duration = meta.get("duration").and_then(Value::as_f64);
    t.track_no = meta.get("track_no").and_then(Value::as_i64);
    if t.title.is_empty() {
        t.title = Path::new(path)
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
    }
    t
}

pub fn track_from_file(path: &Path) -> Track {
    let m = read_tags(path);
    let meta = tags_to_map(&m);
    track_from_meta(&path.to_string_lossy(), &meta)
}

fn tags_to_map(t: &Tags) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("title".into(), json!(t.title));
    m.insert("artist".into(), json!(t.artist));
    m.insert("album".into(), json!(t.album));
    m.insert("duration".into(), json!(t.duration));
    m.insert("track_no".into(), json!(t.track_no));
    m
}

/// Igual que `os.path.getmtime` de Python (mismo redondeo).
fn mtime(path: &Path) -> Option<f64> {
    let d = fs::metadata(path)
        .ok()?
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()?;
    Some(d.as_secs() as f64 + d.subsec_nanos() as f64 * 1e-9)
}

fn normcase(s: &str) -> String {
    if cfg!(windows) {
        s.to_lowercase().replace('/', "\\")
    } else {
        s.to_string()
    }
}

pub fn resolve(path: &str) -> PathBuf {
    let expanded = if let Some(rest) = path.strip_prefix("~") {
        dirs::home_dir()
            .map(|h| h.join(rest.trim_start_matches(['/', '\\'])))
            .unwrap_or_else(|| path.into())
    } else {
        PathBuf::from(path)
    };
    let p = fs::canonicalize(&expanded).unwrap_or(expanded);
    // Windows: quitar el prefijo \\?\ que añade canonicalize.
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) => PathBuf::from(rest),
        None => p,
    }
}

// ------------------------------------------------------------------ biblioteca
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Folder {
    pub name: String,
    pub path: String,
    #[serde(default = "id8")]
    pub id: String,
}

fn id8() -> String {
    new_uid(8)
}

/// Varias bibliotecas (carpetas) con un índice cacheado de metadatos.
#[derive(Debug, Default)]
pub struct Library {
    pub folders: Vec<Folder>,
    /// ruta → {mtime, title, artist, album, duration, track_no, [source_key]}
    pub index: Map<String, Value>,
}

impl Library {
    pub fn path() -> PathBuf {
        data_path("library.json")
    }

    pub fn load() -> Library {
        let raw = read_json(&Self::path()).unwrap_or(Value::Null);
        let folders = raw
            .get("folders")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|f| serde_json::from_value(f.clone()).ok())
                    .collect()
            })
            .unwrap_or_default();
        let index = raw
            .get("index")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        Library { folders, index }
    }

    pub fn save(&self) -> std::io::Result<()> {
        write_json_atomic(
            &Self::path(),
            &json!({"folders": self.folders, "index": self.index}),
        )
    }

    pub fn add_folder(&mut self, path: &str) -> Folder {
        let p = resolve(path).to_string_lossy().into_owned();
        if let Some(f) = self
            .folders
            .iter()
            .find(|f| normcase(&f.path) == normcase(&p))
        {
            return f.clone();
        }
        let name = Path::new(&p)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| p.clone());
        let f = Folder {
            name,
            path: p,
            id: id8(),
        };
        self.folders.push(f.clone());
        let _ = self.save();
        f
    }

    /// Re-escanea todas las carpetas. Solo relee archivos modificados.
    pub fn scan(&mut self) -> usize {
        let mut seen = HashSet::new();
        let roots: Vec<PathBuf> = self
            .folders
            .iter()
            .map(|f| PathBuf::from(&f.path))
            .collect();
        for root in roots {
            if root.is_dir() {
                self.walk(&root, &mut seen);
            }
        }
        self.index.retain(|k, _| seen.contains(k));
        let _ = self.save();
        seen.len()
    }

    fn walk(&mut self, dir: &Path, seen: &mut HashSet<String>) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        let mut entries: Vec<_> = rd.flatten().collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                continue;
            }
            let p = e.path();
            let ft = match e.file_type() {
                Ok(t) => t,
                Err(_) => continue,
            };
            if ft.is_dir() || (ft.is_symlink() && p.is_dir()) {
                self.walk(&p, seen);
                continue;
            }
            if !is_audio(&p) {
                continue;
            }
            let full = p.to_string_lossy().into_owned();
            let Some(mt) = mtime(&p) else { continue };
            seen.insert(full.clone());
            let cached = self.index.get(&full).and_then(Value::as_object);
            if cached.and_then(|c| c.get("mtime")).and_then(Value::as_f64) != Some(mt) {
                let mut entry = Map::new();
                entry.insert("mtime".into(), json!(mt));
                entry.extend(tags_to_map(&read_tags(&p)));
                if let Some(sk) = cached.and_then(|c| c.get("source_key")) {
                    entry.insert("source_key".into(), sk.clone());
                }
                self.index.insert(full, Value::Object(entry));
            }
        }
    }

    pub fn tracks(&self, folder_id: Option<&str>) -> Vec<Track> {
        let prefix = match folder_id {
            Some(id) => match self.folders.iter().find(|f| f.id == id) {
                Some(f) => Some(normcase(&Path::new(&f.path).join("").to_string_lossy())),
                None => return Vec::new(),
            },
            None => None,
        };
        let empty = Map::new();
        let mut tracks: Vec<Track> = self
            .index
            .iter()
            .filter(|(k, _)| {
                prefix
                    .as_ref()
                    .is_none_or(|p| normcase(k).starts_with(p.as_str()))
            })
            .map(|(k, v)| track_from_meta(k, v.as_object().unwrap_or(&empty)))
            .collect();
        tracks.sort_by_cached_key(|t| {
            let artist = if t.artist.is_empty() {
                "~".to_string()
            } else {
                t.artist.to_lowercase()
            };
            (
                artist,
                t.album.to_lowercase(),
                t.track_no.unwrap_or(0),
                t.title.to_lowercase(),
            )
        });
        tracks
    }
}

// ------------------------------------------------------------------ colección
pub const PLAYLIST: &str = "playlist";
pub const ALBUM: &str = "album";
pub const ARTIST: &str = "artist";
pub const FOLDER: &str = "folder";

pub fn kind_icon(kind: &str) -> &'static str {
    match kind {
        ALBUM => "◉",
        ARTIST => "★",
        FOLDER => "◫",
        _ => "☰",
    }
}

fn default_kind() -> String {
    PLAYLIST.into()
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Un elemento guardado: playlist, álbum, artista o carpeta.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Playlist {
    pub name: String,
    pub id: String,
    #[serde(default = "now")]
    pub created: f64,
    #[serde(default)]
    pub source_url: Option<String>,
    #[serde(default = "default_kind")]
    pub kind: String,
    #[serde(default)]
    pub parent: Option<String>,
    #[serde(default)]
    pub artist: String,
    #[serde(default)]
    pub thumb: Option<String>,
    #[serde(default, deserialize_with = "lenient_tracks")]
    pub tracks: Vec<Track>,
}

impl Playlist {
    pub fn duration(&self) -> f64 {
        self.tracks.iter().filter_map(|t| t.duration).sum()
    }
}

fn lenient_tracks<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Vec<Track>, D::Error> {
    let v: Vec<Value> = Vec::deserialize(d)?;
    Ok(v.iter().filter_map(Track::from_value).collect())
}

#[derive(Debug, Default)]
pub struct PlaylistStore {
    pub items: Vec<Playlist>,
}

impl PlaylistStore {
    pub fn path() -> PathBuf {
        data_path("playlists.json")
    }

    pub fn load() -> PlaylistStore {
        let items = read_json(&Self::path())
            .and_then(|v| v.as_array().cloned())
            .map(|a| {
                a.into_iter()
                    .filter_map(|p| serde_json::from_value(p).ok())
                    .collect()
            })
            .unwrap_or_default();
        PlaylistStore { items }
    }

    pub fn save(&self) -> std::io::Result<()> {
        write_json_atomic(&Self::path(), &self.items)
    }

    /// Lo que se puede reproducir (todo menos las carpetas), en el orden de la biblioteca.
    pub fn playable(&self) -> Vec<&Playlist> {
        let order = |k: &str| match k {
            PLAYLIST => 0,
            ALBUM => 1,
            ARTIST => 2,
            _ => 3,
        };
        let mut v: Vec<&Playlist> = self.items.iter().filter(|p| p.kind != FOLDER).collect();
        v.sort_by_key(|p| (order(&p.kind), p.name.to_lowercase()));
        v
    }
}

// ------------------------------------------------------------------ sesión
pub fn session_path() -> PathBuf {
    data_path("session.json")
}

pub fn save_session(tracks: &[Track], index: isize) -> std::io::Result<()> {
    write_json_atomic(&session_path(), &json!({"index": index, "tracks": tracks}))
}

pub fn load_session() -> (Vec<Track>, isize) {
    let raw = read_json(&session_path()).unwrap_or(Value::Null);
    let tracks = raw
        .get("tracks")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Track::from_value).collect())
        .unwrap_or_default();
    let index = raw.get("index").and_then(Value::as_i64).unwrap_or(-1) as isize;
    (tracks, index)
}

/// Lee un `.m3u`/`.m3u8`: rutas locales (relativas a la lista) y títulos `#EXTINF`.
pub fn read_m3u(path: &Path) -> Vec<Track> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let base = path.parent().unwrap_or(Path::new("."));
    let mut out = Vec::new();
    for line in text
        .lines()
        .map(|l| l.trim_start_matches('\u{feff}').trim())
    {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line.starts_with("http://") || line.starts_with("https://") {
            let mut t = Track::new(line);
            t.url = Some(line.to_string());
            out.push(t);
            continue;
        }
        let p = if Path::new(line).is_absolute() {
            PathBuf::from(line)
        } else {
            base.join(line)
        };
        if p.exists() {
            out.push(track_from_file(&p));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefix() {
        assert_eq!(strip_track_prefix("01 - Canción"), "Canción");
        assert_eq!(strip_track_prefix("1.Intro"), "Intro");
        assert_eq!(strip_track_prefix("2001 Odisea"), "2001 Odisea");
        assert_eq!(strip_track_prefix("99problems"), "99problems");
    }

    #[test]
    fn python_playlists_roundtrip() {
        let raw = json!([
            {"name": "Fiesta", "id": "a1b2c3d4", "created": 1.5, "source_url": null,
             "kind": "folder", "parent": null, "artist": "", "thumb": null, "tracks": []},
            {"name": "Mix", "id": "ffff0000", "created": 2.0, "source_url": null,
             "kind": "playlist", "parent": "a1b2c3d4", "artist": "", "thumb": null,
             "tracks": [{"title": "A", "uid": "123456789012"}, {"sin_titulo": 1}]}
        ]);
        let items: Vec<Playlist> = serde_json::from_value(raw).unwrap();
        assert_eq!(items[1].tracks.len(), 1);
        assert_eq!(items[1].parent.as_deref(), Some("a1b2c3d4"));
        let back = serde_json::to_value(&items).unwrap();
        assert_eq!(back[0]["source_url"], Value::Null);
        assert_eq!(
            back[1]["tracks"][0],
            json!({"title": "A", "source": "youtube", "uid": "123456789012"})
        );
    }
}
