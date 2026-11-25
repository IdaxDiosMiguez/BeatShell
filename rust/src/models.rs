//! Modelo de pista común a todas las fuentes (local, YouTube, Spotify).
//!
//! Se serializa exactamente igual que `Track.to_dict()` de la versión Python:
//! los campos vacíos o nulos no se escriben y los desconocidos se ignoran al leer.

use std::path::Path;

use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};

pub const LOCAL: &str = "local";
pub const YOUTUBE: &str = "youtube";
pub const SPOTIFY: &str = "spotify";

pub fn new_uid(len: usize) -> String {
    let a: u64 = rand::random();
    let b: u64 = rand::random();
    let hex = format!("{a:016x}{b:016x}");
    hex[..len.min(hex.len())].to_string()
}

fn uid12() -> String {
    new_uid(12)
}

fn default_source() -> String {
    YOUTUBE.to_string()
}

fn is_empty(s: &str) -> bool {
    s.is_empty()
}

fn opt_empty(s: &Option<String>) -> bool {
    s.as_deref().is_none_or(str::is_empty)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Track {
    pub title: String,
    #[serde(default, skip_serializing_if = "is_empty")]
    pub artist: String,
    #[serde(default, skip_serializing_if = "is_empty")]
    pub album: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration: Option<f64>,
    #[serde(default = "default_source", skip_serializing_if = "is_empty")]
    pub source: String,
    /// Archivo local.
    #[serde(default, skip_serializing_if = "opt_empty")]
    pub path: Option<String>,
    /// Página de YouTube (o resultado de emparejar Spotify).
    #[serde(default, skip_serializing_if = "opt_empty")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "opt_empty")]
    pub spotify_uri: Option<String>,
    /// URL de portada remota.
    #[serde(default, skip_serializing_if = "opt_empty")]
    pub thumb: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub track_no: Option<i64>,
    /// Identificador de *esta entrada* (en cola o playlist), no de la canción.
    #[serde(default = "uid12", skip_serializing_if = "is_empty")]
    pub uid: String,
}

impl Track {
    pub fn new(title: impl Into<String>) -> Self {
        Track {
            title: title.into(),
            artist: String::new(),
            album: String::new(),
            duration: None,
            source: default_source(),
            path: None,
            url: None,
            spotify_uri: None,
            thumb: None,
            track_no: None,
            uid: uid12(),
        }
    }

    /// Lee una pista de un valor JSON; `None` si no es válida (se ignora, como en Python).
    pub fn from_value(v: &serde_json::Value) -> Option<Track> {
        serde_json::from_value(v.clone()).ok()
    }

    /// Identidad de la canción (caché de portadas, coincidencias…). Igual que en Python.
    pub fn key(&self) -> String {
        if let Some(p) = self.path.as_deref().filter(|p| !p.is_empty()) {
            return format!("f:{}", sha1_hex16(p));
        }
        if let Some(uri) = self.spotify_uri.as_deref().filter(|s| !s.is_empty()) {
            return format!("s:{}", uri.rsplit(':').next().unwrap_or(uri));
        }
        if let Some(url) = self.url.as_deref().filter(|s| !s.is_empty()) {
            return format!("y:{}", youtube_id(url).unwrap_or_else(|| sha1_hex16(url)));
        }
        format!(
            "t:{}",
            sha1_hex16(&format!("{}|{}", self.artist, self.title))
        )
    }

    pub fn is_local(&self) -> bool {
        self.path
            .as_deref()
            .is_some_and(|p| !p.is_empty() && Path::new(p).exists())
    }

    pub fn display_artist(&self) -> &str {
        if self.artist.is_empty() {
            "Artista desconocido"
        } else {
            &self.artist
        }
    }

    pub fn search_query(&self) -> String {
        if self.artist.is_empty() {
            self.title.clone()
        } else {
            format!("{} - {}", self.artist, self.title)
        }
    }

    pub fn icon(&self) -> &'static str {
        if self.is_local() {
            return "♫";
        }
        match self.source.as_str() {
            LOCAL => "♫",
            YOUTUBE => "▶",
            SPOTIFY => "●",
            _ => "♪",
        }
    }

    /// Copia con uid nuevo (para insertar la misma canción dos veces).
    pub fn copy_new(&self) -> Track {
        Track {
            uid: uid12(),
            ..self.clone()
        }
    }
}

fn sha1_hex16(s: &str) -> String {
    let digest = Sha1::digest(s.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    hex[..16].to_string()
}

pub fn youtube_id(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    let host = u.host_str().unwrap_or("").to_lowercase();
    let segments: Vec<&str> = u.path().split('/').filter(|p| !p.is_empty()).collect();
    if host.ends_with("youtu.be") {
        return segments.first().map(|s| s.to_string());
    }
    if host.contains("youtube") {
        if let Some((_, v)) = u.query_pairs().find(|(k, _)| k == "v") {
            return Some(v.into_owned());
        }
        if segments.len() >= 2 && ["shorts", "embed", "live", "v"].contains(&segments[0]) {
            return Some(segments[1].to_string());
        }
    }
    None
}

pub fn fmt_time(seconds: Option<f64>) -> String {
    match seconds {
        Some(s) if s.is_finite() && s >= 0.0 => {
            let s = s as u64;
            let (h, rem) = (s / 3600, s % 3600);
            let (m, s) = (rem / 60, rem % 60);
            if h > 0 {
                format!("{h}:{m:02}:{s:02}")
            } else {
                format!("{m}:{s:02}")
            }
        }
        _ => "--:--".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_like_python() {
        let raw = r#"{"title":"Around the World","artist":"Daft Punk","duration":429.0,
                      "source":"youtube","url":"https://www.youtube.com/watch?v=K0HSD_i2DvA",
                      "uid":"abc123def456","extra":"ignorado"}"#;
        let t: Track = serde_json::from_str(raw).unwrap();
        assert_eq!(t.uid, "abc123def456");
        let out = serde_json::to_value(&t).unwrap();
        assert!(out.get("album").is_none());
        assert!(out.get("path").is_none());
        assert_eq!(t.key(), "y:K0HSD_i2DvA");
    }

    #[test]
    fn key_matches_python_sha1() {
        // python: hashlib.sha1("/m/a.mp3".encode()).hexdigest()[:16]
        let mut t = Track::new("x");
        t.path = Some("/m/a.mp3".into());
        assert_eq!(t.key(), "f:7456680d390820a2");
    }

    #[test]
    fn youtube_ids() {
        assert_eq!(youtube_id("https://youtu.be/abc").as_deref(), Some("abc"));
        assert_eq!(
            youtube_id("https://www.youtube.com/shorts/xyz").as_deref(),
            Some("xyz")
        );
        assert_eq!(youtube_id("https://example.com/?v=1"), None);
    }

    #[test]
    fn times() {
        assert_eq!(fmt_time(Some(65.0)), "1:05");
        assert_eq!(fmt_time(Some(3725.0)), "1:02:05");
        assert_eq!(fmt_time(None), "--:--");
        assert_eq!(fmt_time(Some(f64::NAN)), "--:--");
    }
}
