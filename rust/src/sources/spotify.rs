//! Lectura de playlists, álbumes y canciones de Spotify.
//!
//! Puerto de `sources/spotify.py`. Sin configuración usa la página pública de
//! "embed" (no necesita cuenta ni API key). Spotify solo expone ahí las primeras
//! ~100 canciones; para playlists más grandes se pueden poner `spotify_client_id`
//! y `spotify_client_secret` en la configuración y se usará la Web API oficial con
//! paginación.

use std::sync::{LazyLock, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use regex::Regex;
use serde_json::Value;

use super::{Collection, CollectionKind};
use crate::models::{SPOTIFY, Track};

const UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
                  (KHTML, like Gecko) Chrome/131.0 Safari/537.36";
pub const EMBED_LIMIT: usize = 100;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpotifyError(pub String);

impl std::fmt::Display for SpotifyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SpotifyError {}

pub type Result<T> = std::result::Result<T, SpotifyError>;

fn err<T>(msg: impl Into<String>) -> Result<T> {
    Err(SpotifyError(msg.into()))
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// ------------------------------------------------------------------ HTTP
fn client() -> &'static reqwest::blocking::Client {
    static CLIENT: LazyLock<reqwest::blocking::Client> = LazyLock::new(|| {
        reqwest::blocking::Client::builder()
            .user_agent(UA)
            .timeout(Duration::from_secs(20))
            .build()
            .expect("cliente HTTP")
    });
    &CLIENT
}

fn send(req: reqwest::blocking::RequestBuilder) -> Result<String> {
    let resp = req.send().map_err(|e| {
        let reason = std::error::Error::source(&e)
            .map(|s| s.to_string())
            .unwrap_or_else(|| e.to_string());
        SpotifyError(format!("No se pudo conectar con Spotify: {reason}"))
    })?;
    let status = resp.status();
    if status == reqwest::StatusCode::NOT_FOUND {
        return err("Spotify no encuentra ese enlace (¿es privado?)");
    }
    if !status.is_success() {
        return err(format!("Spotify respondió con error {}", status.as_u16()));
    }
    resp.text()
        .map_err(|e| SpotifyError(format!("No se pudo leer la respuesta de Spotify: {e}")))
}

fn get_json(req: reqwest::blocking::RequestBuilder) -> Result<Value> {
    serde_json::from_str(&send(req)?)
        .map_err(|_| SpotifyError("Spotify devolvió datos ilegibles".into()))
}

// ------------------------------------------------------------------ util
fn s<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str).filter(|x| !x.is_empty())
}

fn arr<'a>(v: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    v.get(key).and_then(Value::as_array).into_iter().flatten()
}

fn artists(subtitle: &str) -> String {
    subtitle
        .replace('\u{a0}', " ")
        .split(',')
        .map(str::trim)
        .filter(|a| !a.is_empty())
        .collect::<Vec<_>>()
        .join(", ")
}

fn names(v: &Value, key: &str) -> String {
    arr(v, key)
        .filter_map(|a| s(a, "name"))
        .collect::<Vec<_>>()
        .join(", ")
}

fn best_image(sources: Option<&Value>) -> Option<String> {
    let best = sources?
        .as_array()?
        .iter()
        .max_by_key(|s| s.get("width").and_then(Value::as_u64).unwrap_or(0))?;
    let url = s(best, "url")?;
    // El embed da portadas de 300 px; el mismo id con este prefijo es la de 640 px.
    Some(url.replace("ab67616d00001e02", "ab67616d0000b273"))
}

fn first_image(v: &Value) -> Option<String> {
    arr(v, "images")
        .next()
        .and_then(|i| s(i, "url"))
        .map(String::from)
}

fn spotify_track(title: &str) -> Track {
    let mut t = Track::new(title);
    t.source = SPOTIFY.into();
    t
}

// ------------------------------------------------------------------ embed
/// Lee el JSON `__NEXT_DATA__` de la página de embed.
pub fn parse_embed(html: &str, kind: &str, sid: &str) -> Result<Collection> {
    static NEXT: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?s)<script id="__NEXT_DATA__" type="application/json">(.*?)</script>"#)
            .unwrap()
    });
    let Some(m) = NEXT.captures(html) else {
        return err("Spotify cambió su página; configura credenciales de API");
    };
    let data: Value = serde_json::from_str(&m[1])
        .map_err(|_| SpotifyError("No se pudo leer el contenido de Spotify".into()))?;
    let ent = data
        .pointer("/props/pageProps/state/data/entity")
        .filter(|e| e.is_object());
    let Some(ent) = ent else {
        return err("No se pudo leer el contenido de Spotify");
    };

    let name = s(ent, "name")
        .or_else(|| s(ent, "title"))
        .unwrap_or("Spotify")
        .to_string();
    let cover = best_image(ent.pointer("/coverArt/sources"))
        .or_else(|| best_image(ent.pointer("/visualIdentity/image")));
    let mut album = if kind == "album" {
        name.clone()
    } else {
        String::new()
    };

    let single;
    let items: Vec<&Value> = match ent.get("trackList").and_then(Value::as_array) {
        Some(list) => list.iter().collect(),
        None => {
            // una sola canción
            let a = names(ent, "artists");
            single = serde_json::json!({
                "uri": ent.get("uri"),
                "title": name,
                "subtitle": if a.is_empty() { s(ent, "subtitle").unwrap_or("").to_string() } else { a },
                "duration": ent.get("duration"),
            });
            album.clear();
            vec![&single]
        }
    };
    let mut tracks = Vec::new();
    for (i, it) in items.iter().enumerate() {
        let Some(title) = s(it, "title") else {
            continue;
        };
        let mut t = spotify_track(title);
        t.artist = artists(s(it, "subtitle").unwrap_or(""));
        t.album = album.clone();
        t.duration = it
            .get("duration")
            .and_then(Value::as_f64)
            .filter(|d| *d > 0.0)
            .map(|d| d / 1000.0);
        t.spotify_uri = s(it, "uri").map(String::from);
        t.thumb = if kind != "playlist" {
            cover.clone()
        } else {
            None
        };
        t.track_no = (kind == "album").then_some(i as i64 + 1);
        tracks.push(t);
    }
    Ok(Collection {
        name,
        tracks,
        kind: if kind == "album" {
            CollectionKind::Album
        } else {
            CollectionKind::Playlist
        },
        source: SPOTIFY.into(),
        owner: artists(s(ent, "subtitle").unwrap_or("")),
        thumb: cover,
        url: Some(format!("https://open.spotify.com/{kind}/{sid}")),
    })
}

fn from_embed(kind: &str, sid: &str) -> Result<Collection> {
    let html = send(client().get(format!("https://open.spotify.com/embed/{kind}/{sid}")))?;
    parse_embed(&html, kind, sid)
}

// ------------------------------------------------------------------ Web API
static TOKEN: Mutex<Option<(String, f64)>> = Mutex::new(None);

fn token(client_id: &str, secret: &str) -> Result<String> {
    if let Some((tok, exp)) = TOKEN.lock().unwrap().as_ref()
        && *exp > unix_now()
    {
        return Ok(tok.clone());
    }
    let auth = base64::engine::general_purpose::STANDARD.encode(format!("{client_id}:{secret}"));
    let data = get_json(
        client()
            .post("https://accounts.spotify.com/api/token")
            .header("Authorization", format!("Basic {auth}"))
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body("grant_type=client_credentials"),
    )?;
    let Some(tok) = s(&data, "access_token") else {
        return err("Spotify no dio un token de acceso");
    };
    let expires = data
        .get("expires_in")
        .and_then(Value::as_f64)
        .unwrap_or(3600.0);
    *TOKEN.lock().unwrap() = Some((tok.to_string(), unix_now() + expires - 60.0));
    Ok(tok.to_string())
}

fn api(path: &str, tok: &str) -> Result<Value> {
    let url = if path.starts_with("http") {
        path.to_string()
    } else {
        format!("https://api.spotify.com/v1/{path}")
    };
    get_json(client().get(url).bearer_auth(tok))
}

/// Un objeto `track` de la Web API como pista.
pub fn api_track(t: &Value, album: Option<&Value>) -> Option<Track> {
    let name = s(t, "name")?;
    let alb = album.or_else(|| t.get("album")).unwrap_or(&Value::Null);
    let mut tr = spotify_track(name);
    tr.artist = names(t, "artists");
    tr.album = s(alb, "name").unwrap_or("").to_string();
    tr.duration = t
        .get("duration_ms")
        .and_then(Value::as_f64)
        .filter(|d| *d > 0.0)
        .map(|d| d / 1000.0);
    tr.spotify_uri = s(t, "uri").map(String::from);
    tr.thumb = first_image(alb);
    tr.track_no = t.get("track_number").and_then(Value::as_i64);
    Some(tr)
}

/// Recorre una página paginada (`items` + `next`) y todas las siguientes.
fn paginate(mut page: Value, tok: &str, mut each: impl FnMut(&Value)) -> Result<()> {
    loop {
        arr(&page, "items").for_each(&mut each);
        let Some(next) = s(&page, "next").map(String::from) else {
            return Ok(());
        };
        page = api(&next, tok)?;
    }
}

fn from_api(kind: &str, sid: &str, client_id: &str, secret: &str) -> Result<Collection> {
    let tok = token(client_id, secret)?;
    match kind {
        "track" => {
            let t = api_track(&api(&format!("tracks/{sid}"), &tok)?, None);
            Ok(Collection {
                name: t
                    .as_ref()
                    .map(|t| t.title.clone())
                    .unwrap_or_else(|| "Canción".into()),
                thumb: t.as_ref().and_then(|t| t.thumb.clone()),
                tracks: t.into_iter().collect(),
                kind: CollectionKind::Playlist,
                source: SPOTIFY.into(),
                ..Default::default()
            })
        }
        "album" => {
            let alb = api(&format!("albums/{sid}"), &tok)?;
            let mut tracks = Vec::new();
            let first = alb.get("tracks").cloned().unwrap_or(Value::Null);
            paginate(first, &tok, |t| tracks.extend(api_track(t, Some(&alb))))?;
            Ok(Collection {
                name: s(&alb, "name").unwrap_or("Álbum").to_string(),
                tracks,
                kind: CollectionKind::Album,
                source: SPOTIFY.into(),
                owner: names(&alb, "artists"),
                thumb: first_image(&alb),
                url: Some(format!("https://open.spotify.com/album/{sid}")),
            })
        }
        _ => {
            let pl = api(
                &format!("playlists/{sid}?fields=name,owner(display_name),images"),
                &tok,
            )?;
            let fields = "items(track(name,uri,duration_ms,track_number,artists(name),album(name,images))),next";
            let mut url = url::Url::parse(&format!(
                "https://api.spotify.com/v1/playlists/{sid}/tracks"
            ))
            .unwrap();
            url.query_pairs_mut()
                .append_pair("limit", "100")
                .append_pair("fields", fields);
            let mut tracks = Vec::new();
            paginate(api(url.as_str(), &tok)?, &tok, |it| {
                if let Some(t) = it.get("track") {
                    tracks.extend(api_track(t, None));
                }
            })?;
            Ok(Collection {
                name: s(&pl, "name").unwrap_or("Playlist").to_string(),
                tracks,
                kind: CollectionKind::Playlist,
                source: SPOTIFY.into(),
                owner: pl
                    .pointer("/owner/display_name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                thumb: first_image(&pl),
                url: Some(format!("https://open.spotify.com/playlist/{sid}")),
            })
        }
    }
}

/// `reference` es "playlist:ID", "album:ID" o "track:ID" (lo que da `classify`).
pub fn fetch(reference: &str, client_id: &str, secret: &str) -> Result<Collection> {
    let Some((kind, sid)) = reference.split_once(':') else {
        return err("Enlace de Spotify no válido");
    };
    if !client_id.is_empty() && !secret.is_empty() {
        // p. ej. playlists editoriales bloqueadas en la API → probamos el embed
        if let Ok(c) = from_api(kind, sid, client_id, secret) {
            return Ok(c);
        }
    }
    from_embed(kind, sid)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn page(entity: Value) -> String {
        let data = json!({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}});
        format!(
            "<html><script id=\"__NEXT_DATA__\" type=\"application/json\">{data}</script></html>"
        )
    }

    #[test]
    fn embed_album() {
        let html = page(json!({
            "name": "Discovery", "subtitle": "Daft\u{a0}Punk",
            "coverArt": {"sources": [
                {"url": "https://i.scdn.co/image/ab67616d00001e02aaa", "width": 300},
                {"url": "https://i.scdn.co/image/small", "width": 64}]},
            "trackList": [
                {"uri": "spotify:track:1", "title": "One More Time", "subtitle": "Daft Punk", "duration": 320357},
                {"uri": "spotify:track:x", "title": ""},
                {"uri": "spotify:track:2", "title": "Aerodynamic", "subtitle": "Daft Punk, Otro ", "duration": 212000}
            ]
        }));
        let c = parse_embed(&html, "album", "ID").unwrap();
        assert_eq!(c.kind, CollectionKind::Album);
        assert_eq!(c.owner, "Daft Punk");
        assert_eq!(
            c.thumb.as_deref(),
            Some("https://i.scdn.co/image/ab67616d0000b273aaa")
        );
        assert_eq!(c.url.as_deref(), Some("https://open.spotify.com/album/ID"));
        assert_eq!(c.tracks.len(), 2);
        let t = &c.tracks[1];
        assert_eq!(t.artist, "Daft Punk, Otro");
        assert_eq!(t.album, "Discovery");
        assert_eq!(t.track_no, Some(3));
        assert_eq!(t.duration, Some(212.0));
        assert_eq!(t.source, SPOTIFY);
        assert_eq!(t.thumb, c.thumb);
    }

    #[test]
    fn embed_playlist_and_track() {
        let html = page(json!({"title": "Lista", "subtitle": "Yo",
            "visualIdentity": {"image": [{"url": "img", "width": 640}]},
            "trackList": [{"uri": "u", "title": "A", "subtitle": "B"}]}));
        let c = parse_embed(&html, "playlist", "P").unwrap();
        assert_eq!(c.name, "Lista");
        assert_eq!(c.thumb.as_deref(), Some("img"));
        assert!(c.tracks[0].thumb.is_none());
        assert!(c.tracks[0].track_no.is_none());

        let html = page(
            json!({"name": "Solo", "uri": "spotify:track:9", "duration": 1000,
            "artists": [{"name": "A1"}, {"name": "A2"}]}),
        );
        let c = parse_embed(&html, "track", "9").unwrap();
        assert_eq!(c.tracks.len(), 1);
        let t = &c.tracks[0];
        assert_eq!((t.title.as_str(), t.artist.as_str()), ("Solo", "A1, A2"));
        assert_eq!(t.spotify_uri.as_deref(), Some("spotify:track:9"));
        assert_eq!(t.album, "");
    }

    #[test]
    fn embed_errors() {
        assert!(
            parse_embed("<html></html>", "album", "x")
                .unwrap_err()
                .0
                .contains("cambió")
        );
        assert!(parse_embed(&page(Value::Null), "album", "x").is_err());
    }

    #[test]
    fn web_api_track() {
        let t = api_track(
            &json!({"name": "N", "uri": "spotify:track:1", "duration_ms": 1500, "track_number": 4,
                "artists": [{"name": "X"}, {"name": "Y"}],
                "album": {"name": "Al", "images": [{"url": "big"}, {"url": "small"}]}}),
            None,
        )
        .unwrap();
        assert_eq!(t.artist, "X, Y");
        assert_eq!(t.album, "Al");
        assert_eq!(t.thumb.as_deref(), Some("big"));
        assert_eq!(t.duration, Some(1.5));
        assert_eq!(t.track_no, Some(4));
        assert!(api_track(&json!({"name": ""}), None).is_none());
        assert!(api_track(&Value::Null, None).is_none());
    }

    #[test]
    fn bad_reference() {
        assert!(fetch("nada", "", "").is_err());
    }
}
