//! Fuentes de música: YouTube, Spotify y detección de enlaces.
//!
//! Puerto de `sources/__init__.py`. YouTube se resuelve con el binario `yt-dlp`
//! (no se reimplementa: cambia demasiado a menudo) y Spotify con su página de
//! embed o, si hay credenciales, con la Web API.

pub mod spotify;
pub mod youtube;

use crate::models::Track;
use regex::Regex;
use std::sync::LazyLock;

/// Una lista de pistas que viene de fuera: playlist o álbum.
#[derive(Debug, Clone, Default)]
pub struct Collection {
    pub name: String,
    pub tracks: Vec<Track>,
    pub kind: CollectionKind,
    /// "youtube" | "spotify"
    pub source: String,
    pub owner: String,
    pub thumb: Option<String>,
    pub url: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum CollectionKind {
    #[default]
    Playlist,
    Album,
    Search,
}

/// Lo que el usuario escribió en la barra de búsqueda.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Query {
    /// `kind:id`, con kind en track | album | playlist.
    Spotify(String),
    YoutubePlaylist(String),
    YoutubeVideo(String),
    Url(String),
    Search(String),
}

static SPOTIFY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?|spotify:)(?P<kind>track|album|playlist)[/:](?P<id>[A-Za-z0-9]{10,})",
    )
    .unwrap()
});

/// Clasifica lo que el usuario escribió en la barra.
pub fn classify(text: &str) -> Query {
    let t = text.trim();
    if let Some(m) = SPOTIFY_RE.captures(t) {
        return Query::Spotify(format!("{}:{}", &m["kind"], &m["id"]));
    }
    if t.starts_with("http://") || t.starts_with("https://") {
        let Ok(u) = url::Url::parse(t) else {
            return Query::Url(t.into());
        };
        let host = u.host_str().unwrap_or("").to_lowercase();
        if host.contains("youtube") || host.ends_with("youtu.be") {
            let q = |key: &str| {
                u.query_pairs()
                    .find(|(k, v)| k == key && !v.is_empty())
                    .map(|(_, v)| v.into_owned())
            };
            let (list, v) = (q("list"), q("v"));
            if let Some(list) = list {
                if u.path().starts_with("/playlist") || v.is_none() {
                    return Query::YoutubePlaylist(t.into());
                }
                if !list.starts_with("RD") && !list.starts_with("UL") {
                    // vídeo dentro de una playlist: tratamos la playlist completa
                    return Query::YoutubePlaylist(t.into());
                }
            }
            return Query::YoutubeVideo(t.into());
        }
        return Query::Url(t.into());
    }
    Query::Search(t.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_links() {
        assert_eq!(
            classify("https://open.spotify.com/intl-es/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x"),
            Query::Spotify("playlist:37i9dQZF1DXcBWIGoYBM5M".into())
        );
        assert_eq!(
            classify("spotify:track:4uLU6hMCjMI75M1A2tKUQC"),
            Query::Spotify("track:4uLU6hMCjMI75M1A2tKUQC".into())
        );
        assert!(matches!(
            classify("https://www.youtube.com/playlist?list=PL123"),
            Query::YoutubePlaylist(_)
        ));
        assert!(matches!(
            classify("https://www.youtube.com/watch?v=a&list=PL123"),
            Query::YoutubePlaylist(_)
        ));
        assert!(matches!(
            classify("https://www.youtube.com/watch?v=a&list=RDa"),
            Query::YoutubeVideo(_)
        ));
        assert!(matches!(
            classify("https://youtu.be/abc"),
            Query::YoutubeVideo(_)
        ));
        assert!(matches!(
            classify("https://example.com/a.mp3"),
            Query::Url(_)
        ));
        assert_eq!(classify("  bad bunny  "), Query::Search("bad bunny".into()));
    }
}
