//! YouTube vía yt-dlp: búsqueda, playlists, streams y emparejado de canciones.
//!
//! Puerto de `sources/youtube.py`. En vez de la API de Python de yt-dlp se llama
//! al binario `yt-dlp` con `-J` (JSON en stdout). Se busca en `BEATSHELL_YTDLP` o
//! en el PATH.

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::Value;

use super::{Collection, CollectionKind};
use crate::audio::Source;
use crate::ffmpeg;
use crate::models::{Track, YOUTUBE, youtube_id};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct YouTubeError(pub String);

impl std::fmt::Display for YouTubeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for YouTubeError {}

pub type Result<T> = std::result::Result<T, YouTubeError>;

fn err<T>(msg: impl Into<String>) -> Result<T> {
    Err(YouTubeError(msg.into()))
}

pub const NOT_FOUND: &str = "No se encontró yt-dlp. Instálalo (https://github.com/yt-dlp/yt-dlp) \
                             o indica su ruta en la variable BEATSHELL_YTDLP.";

// Etiquetas que ensucian los títulos de YouTube y no aportan nada.
static NOISE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\s*[\(\[\{][^\)\]\}]*?\b(official|oficial|video|vídeo|videoclip|audio|lyrics?|letra|visuali[sz]er|hd|hq|4k|mv|m/v|clip)\b[^\)\]\}]*[\)\]\}]",
    )
    .unwrap()
});

const BAD_WORDS: &[&str] = &[
    "live",
    "en vivo",
    "directo",
    "cover",
    "karaoke",
    "instrumental",
    "remix",
    "sped up",
    "slowed",
    "reverb",
    "8d",
    "nightcore",
    "tutorial",
    "reaction",
    "lesson",
    "acoustic",
    "bass boosted",
    "1 hour",
    "loop",
];

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// ------------------------------------------------------------------ yt-dlp
pub fn ytdlp_exe() -> Option<&'static PathBuf> {
    static EXE: OnceLock<Option<PathBuf>> = OnceLock::new();
    EXE.get_or_init(|| {
        if let Some(p) = std::env::var_os("BEATSHELL_YTDLP")
            .map(PathBuf::from)
            .filter(|p| p.is_file())
        {
            return Some(p);
        }
        let name = if cfg!(windows) {
            "yt-dlp.exe"
        } else {
            "yt-dlp"
        };
        ffmpeg::which(name)
    })
    .as_ref()
}

/// Runtimes de JavaScript que yt-dlp puede usar para descifrar las firmas.
fn js_runtimes() -> &'static [&'static str] {
    static FOUND: OnceLock<Vec<&'static str>> = OnceLock::new();
    FOUND.get_or_init(|| {
        let ext = if cfg!(windows) { ".exe" } else { "" };
        ["deno", "node", "bun"]
            .into_iter()
            .filter(|n| ffmpeg::which(&format!("{n}{ext}")).is_some())
            .collect()
    })
}

/// Opciones comunes a todas las llamadas (equivalente a `base_opts`).
fn base_command() -> Result<Command> {
    let Some(exe) = ytdlp_exe() else {
        return err(NOT_FOUND);
    };
    let mut cmd = ffmpeg::command(exe);
    cmd.args([
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        "20",
        "--no-config-locations",
    ]);
    for rt in js_runtimes() {
        cmd.args(["--js-runtimes", rt]);
    }
    if let Some(ff) = ffmpeg::ffmpeg_exe() {
        cmd.arg("--ffmpeg-location").arg(ff);
    }
    cmd.stdin(Stdio::null());
    Ok(cmd)
}

/// Convierte "ERROR: [youtube] abc: Video unavailable" en "abc: Video unavailable".
pub fn clean_error(stderr: &str) -> String {
    static PREFIX: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^ERROR:\s*(\[[^\]]+\]\s*)?").unwrap());
    let line = stderr
        .lines()
        .find(|l| l.starts_with("ERROR:"))
        .or_else(|| stderr.lines().rev().find(|l| !l.trim().is_empty()))
        .unwrap_or("");
    let msg: String = PREFIX.replace(line, "").chars().take(240).collect();
    if msg.is_empty() {
        "yt-dlp falló sin dar detalles".into()
    } else {
        msg
    }
}

/// `yt-dlp -J <extra> <target>` y su JSON.
fn extract(target: &str, extra: &[&str]) -> Result<Value> {
    let mut cmd = base_command()?;
    cmd.arg("-J").args(extra).arg("--").arg(target);
    let out = cmd
        .output()
        .map_err(|e| YouTubeError(format!("No se pudo ejecutar yt-dlp: {e}")))?;
    if !out.status.success() {
        return err(clean_error(&String::from_utf8_lossy(&out.stderr)));
    }
    match serde_json::from_slice::<Value>(&out.stdout) {
        Ok(v) if v.is_object() => Ok(v),
        _ => err("YouTube no devolvió resultados"),
    }
}

// ------------------------------------------------------------------ JSON
fn s<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str).filter(|x| !x.is_empty())
}

fn f(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64).filter(|x| *x > 0.0)
}

// ------------------------------------------------------------------ títulos
pub fn clean_title(title: &str) -> String {
    static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s{2,}").unwrap());
    let t = NOISE.replace_all(title, "");
    let t = SPACES.replace_all(t.trim(), " ");
    let t = t
        .trim()
        .trim_matches(|c| matches!(c, ' ' | '-' | '|' | '·'));
    if t.is_empty() {
        title.trim().to_string()
    } else {
        t.to_string()
    }
}

pub fn clean_channel(channel: &str) -> String {
    static TOPIC: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s*-\s*Topic$").unwrap());
    let c = TOPIC.replace(channel.trim(), "");
    c.strip_suffix("VEVO").unwrap_or(&c).trim().to_string()
}

/// "Artista - Canción (Official Video)" → ("Artista", "Canción").
pub fn split_artist_title(raw_title: &str, channel: &str) -> (String, String) {
    let title = clean_title(raw_title);
    for sep in [" - ", " – ", " — "] {
        if let Some((a, t)) = title.split_once(sep)
            && !a.is_empty()
            && !t.is_empty()
        {
            return (a.trim().to_string(), t.trim().to_string());
        }
    }
    (clean_channel(channel), title)
}

pub fn thumb_for(video_id: Option<&str>) -> Option<String> {
    video_id.map(|id| format!("https://i.ytimg.com/vi/{id}/mqdefault.jpg"))
}

/// Una entrada de búsqueda/playlist (`--flat-playlist`) como pista.
pub fn entry_to_track(e: &Value) -> Option<Track> {
    let vid = s(e, "id")?;
    if s(e, "ie_key").is_some_and(|k| k != "Youtube") {
        return None; // canales o playlists dentro de resultados
    }
    let title = s(e, "title").unwrap_or("");
    if matches!(title, "[Private video]" | "[Deleted video]") {
        return None;
    }
    let channel = s(e, "channel").or_else(|| s(e, "uploader")).unwrap_or("");
    let (artist, name) = split_artist_title(title, channel);
    let mut t = Track::new(if name.is_empty() {
        title.to_string()
    } else {
        name
    });
    t.artist = artist;
    t.duration = f(e, "duration");
    t.source = YOUTUBE.into();
    t.url = Some(format!("https://www.youtube.com/watch?v={vid}"));
    t.thumb = thumb_for(Some(vid));
    Some(t)
}

fn entries(info: &Value) -> impl Iterator<Item = &Value> {
    info.get("entries")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|e| e.is_object())
}

// ------------------------------------------------------------------ búsqueda
pub fn parse_search(info: &Value) -> Vec<Track> {
    entries(info).filter_map(entry_to_track).collect()
}

pub fn search(query: &str, limit: usize) -> Result<Vec<Track>> {
    let info = extract(&format!("ytsearch{limit}:{query}"), &["--flat-playlist"])?;
    Ok(parse_search(&info))
}

pub fn parse_playlist(info: &Value, url: &str) -> Collection {
    let tracks: Vec<Track> = entries(info).filter_map(entry_to_track).collect();
    let thumb = info
        .get("thumbnails")
        .and_then(Value::as_array)
        .and_then(|a| a.last())
        .and_then(|t| s(t, "url"))
        .map(String::from)
        .or_else(|| tracks.first().and_then(|t| t.thumb.clone()));
    Collection {
        name: s(info, "title")
            .unwrap_or("Playlist de YouTube")
            .to_string(),
        kind: CollectionKind::Playlist,
        source: YOUTUBE.into(),
        owner: clean_channel(
            s(info, "channel")
                .or_else(|| s(info, "uploader"))
                .unwrap_or(""),
        ),
        thumb,
        url: Some(url.to_string()),
        tracks,
    }
}

pub fn playlist(url: &str) -> Result<Collection> {
    let info = extract(url, &["--flat-playlist", "--yes-playlist"])?;
    Ok(parse_playlist(&info, url))
}

// ------------------------------------------------------------------ streams
const FORMAT: &str = "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best";

// Clientes de YouTube que sirven el audio al momento (sin la espera del anuncio).
// Solo ofrecen formatos mezclados (AAC ~96-128 kbps): se usan para arrancar ya y
// luego el motor cambia, sin cortes, al stream de alta calidad (Opus).
const FAST_CLIENTS: &[&str] = &["tv_simply", "web_embedded"];
const FAST_FORMAT: &str = "bestaudio/18/best[height<=480]/best";

/// Datos del vídeo que acompañan al stream.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct StreamMeta {
    pub title: Option<String>,
    pub channel: Option<String>,
    pub duration: Option<f64>,
    pub id: Option<String>,
    pub thumbnail: Option<String>,
    pub is_live: bool,
}

/// Un stream de audio resuelto.
#[derive(Debug, Clone)]
pub struct Stream {
    pub source: Source,
    /// Viene de un cliente rápido (menos calidad): conviene cambiar a HQ en marcha.
    pub fast: bool,
    pub meta: StreamMeta,
}

struct Cached {
    stream: Stream,
    expires: f64,
}

static CACHE: LazyLock<Mutex<HashMap<String, Cached>>> = LazyLock::new(Default::default);

/// Las URLs de googlevideo llevan `expire=<unix>`; se usan hasta 10 min antes.
fn expiry(url: &str) -> f64 {
    url::Url::parse(url)
        .ok()
        .and_then(|u| {
            u.query_pairs()
                .find(|(k, _)| k == "expire")
                .and_then(|(_, v)| v.parse::<f64>().ok())
        })
        .map(|e| e - 600.0)
        .unwrap_or_else(|| unix_now() + 3.0 * 3600.0)
}

fn cache_key(url: &str, fast: bool) -> String {
    format!(
        "{}|{}",
        youtube_id(url).unwrap_or_else(|| url.to_string()),
        if fast { "fast" } else { "hq" }
    )
}

/// El stream ya resuelto (si sigue vigente), sin hacer peticiones.
pub fn cached_stream(url: &str, fast: bool) -> Option<Stream> {
    let cache = CACHE.lock().unwrap();
    cache
        .get(&cache_key(url, fast))
        .filter(|c| c.expires > unix_now())
        .map(|c| c.stream.clone())
}

/// Convierte el JSON de un vídeo (con formato elegido) en un stream.
pub fn parse_stream(info: &Value, fast: bool) -> Result<Stream> {
    let requested: Vec<&Value> = info
        .get("requested_formats")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .collect();
    let Some(input) = s(info, "url").or_else(|| requested.first().and_then(|r| s(r, "url"))) else {
        return err("No hay un stream de audio disponible para este vídeo");
    };
    let headers = info
        .get("http_headers")
        .and_then(Value::as_object)
        .map(|h| {
            h.iter()
                .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                .collect()
        })
        .unwrap_or_default();
    let available_at = std::iter::once(info)
        .chain(requested.iter().copied())
        .filter_map(|v| f(v, "available_at"))
        .fold(0.0, f64::max);
    let duration = f(info, "duration");
    Ok(Stream {
        source: Source {
            input: input.to_string(),
            headers,
            duration,
            available_at,
        },
        fast,
        meta: StreamMeta {
            title: s(info, "title").map(String::from),
            channel: s(info, "channel")
                .or_else(|| s(info, "uploader"))
                .map(String::from),
            duration,
            id: s(info, "id").map(String::from),
            thumbnail: s(info, "thumbnail").map(String::from),
            is_live: info
                .get("is_live")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        },
    })
}

/// Resuelve la URL directa de audio (con caché hasta que caduca).
///
/// `fast` usa clientes sin espera de anuncio (arranque inmediato, algo menos de calidad).
pub fn stream_info(url: &str, fast: bool) -> Result<Stream> {
    if let Some(hit) = cached_stream(url, fast) {
        return Ok(hit);
    }
    let info = if fast {
        // Un cliente cada vez: pedirlos juntos hace que yt-dlp consulte ambos (el doble de lento).
        let mut last = YouTubeError("No hay stream rápido".into());
        let mut found = None;
        for client in FAST_CLIENTS {
            let args = format!("youtube:player_client={client}");
            match extract(
                url,
                &[
                    "-f",
                    FAST_FORMAT,
                    "--no-playlist",
                    "--extractor-args",
                    &args,
                ],
            ) {
                Ok(v) => {
                    found = Some(v);
                    break;
                }
                Err(e) => last = e,
            }
        }
        found.ok_or(last)?
    } else {
        extract(url, &["-f", FORMAT, "--no-playlist"])?
    };
    let stream = parse_stream(&info, fast)?;
    if fast && stream.source.available_at > unix_now() + 0.5 {
        return err("El stream rápido también exige esperar");
    }
    CACHE.lock().unwrap().insert(
        cache_key(url, fast),
        Cached {
            expires: expiry(&stream.source.input),
            stream: stream.clone(),
        },
    );
    Ok(stream)
}

/// La pista de un vídeo suelto (para enlaces pegados en la barra).
pub fn video_track(url: &str) -> Result<Track> {
    let meta = stream_info(url, false)?.meta;
    let (artist, name) = split_artist_title(
        meta.title.as_deref().unwrap_or(url),
        meta.channel.as_deref().unwrap_or(""),
    );
    let mut t = Track::new(name);
    t.artist = artist;
    t.duration = meta.duration;
    t.source = YOUTUBE.into();
    t.url = Some(match &meta.id {
        Some(id) => format!("https://www.youtube.com/watch?v={id}"),
        None => url.to_string(),
    });
    t.thumb = thumb_for(meta.id.as_deref());
    Ok(t)
}

// ------------------------------------------------------------------ emparejar
fn words(text: &str) -> HashSet<String> {
    static PUNCT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\w\s]").unwrap());
    PUNCT
        .replace_all(&text.to_lowercase(), " ")
        .split_whitespace()
        .map(String::from)
        .collect()
}

/// Igual que `difflib.SequenceMatcher(None, a, b).ratio()` (Ratcliff/Obershelp).
pub fn similarity(a: &str, b: &str) -> f64 {
    fn matches(a: &[char], b: &[char]) -> usize {
        let (mut best, mut ai, mut bj) = (0, 0, 0);
        // Subcadena común más larga (programación dinámica en una fila).
        let mut prev = vec![0usize; b.len() + 1];
        #[allow(clippy::needless_range_loop)]
        for i in 0..a.len() {
            let mut cur = vec![0usize; b.len() + 1];
            for j in 0..b.len() {
                if a[i] == b[j] {
                    cur[j + 1] = prev[j] + 1;
                    if cur[j + 1] > best {
                        best = cur[j + 1];
                        ai = i + 1 - best;
                        bj = j + 1 - best;
                    }
                }
            }
            prev = cur;
        }
        if best == 0 {
            return 0;
        }
        best + matches(&a[..ai], &b[..bj]) + matches(&a[ai + best..], &b[bj + best..])
    }
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let total = a.len() + b.len();
    if total == 0 {
        1.0
    } else {
        2.0 * matches(&a, &b) as f64 / total as f64
    }
}

pub fn match_score(
    candidate: &Track,
    raw_title: &str,
    channel: &str,
    target: &Track,
    rank: usize,
) -> f64 {
    let mut score = -(rank as f64) * 1.5;
    if let (Some(td), Some(cd)) = (target.duration, candidate.duration) {
        let diff = (cd - td).abs();
        score += if diff <= 15.0 {
            30.0 - diff * 2.0
        } else {
            -20.0 - diff.min(120.0) / 4.0
        };
    }
    let tw = words(&target.title);
    let cw = words(raw_title);
    if !tw.is_empty() {
        score += 40.0 * tw.intersection(&cw).count() as f64 / tw.len() as f64;
    }
    score += 12.0
        * similarity(
            &target.title.to_lowercase(),
            &candidate.title.to_lowercase(),
        );
    let main_artist = target
        .artist
        .split(',')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    let hay = format!("{raw_title} {channel}").to_lowercase();
    if !main_artist.is_empty() && hay.contains(&main_artist) {
        score += 20.0;
    }
    if channel.ends_with("- Topic") {
        score += 12.0;
    }
    let raw_lower = raw_title.to_lowercase();
    if raw_lower.contains("official audio") || raw_lower.contains("audio oficial") {
        score += 6.0;
    }
    let target_text = format!("{} {}", target.title, target.album).to_lowercase();
    for bad in BAD_WORDS {
        if raw_lower.contains(bad) && !target_text.contains(bad) {
            score -= 25.0;
        }
    }
    score
}

/// Elige el mejor candidato de una búsqueda para `target`.
pub fn best_match(info: &Value, target: &Track) -> Option<Track> {
    let mut best: Option<(f64, Track)> = None;
    for (rank, e) in entries(info).enumerate() {
        let Some(cand) = entry_to_track(e) else {
            continue;
        };
        let sc = match_score(
            &cand,
            s(e, "title").unwrap_or(""),
            s(e, "channel").unwrap_or(""),
            target,
            rank,
        );
        if best.as_ref().is_none_or(|(b, _)| sc > *b) {
            best = Some((sc, cand));
        }
    }
    best.map(|(_, t)| t)
}

/// Busca en YouTube la mejor versión de una canción (p. ej. de Spotify).
pub fn find_match(target: &Track) -> Result<Track> {
    let query = target.search_query();
    let info = extract(&format!("ytsearch6:{query}"), &["--flat-playlist"])?;
    best_match(&info, target)
        .ok_or_else(|| YouTubeError(format!("No se encontró «{query}» en YouTube")))
}

// ------------------------------------------------------------------ descargas
const PROGRESS_TAG: &str = "BSPROG";

/// Interpreta una línea de progreso de `--progress-template`.
fn parse_progress(line: &str) -> Option<f64> {
    let rest = line.trim().strip_prefix(PROGRESS_TAG)?;
    let nums: Vec<Option<f64>> = rest
        .split_whitespace()
        .map(|x| x.parse::<f64>().ok())
        .collect();
    let done = nums.first().copied().flatten().unwrap_or(0.0);
    let total = nums
        .get(1)
        .copied()
        .flatten()
        .or(nums.get(2).copied().flatten())
        .unwrap_or(0.0);
    Some(if total > 0.0 {
        (0.9 * done / total).min(0.9)
    } else {
        0.05
    })
}

/// Descarga el audio y lo convierte a MP3. Devuelve (ruta_mp3, info).
///
/// `progress(fracción, etapa)` se llama desde este mismo hilo.
pub fn download_audio(
    url: &str,
    out_base: &str,
    quality: &str,
    mut progress: impl FnMut(f64, &str),
) -> Result<(String, Value)> {
    let mut cmd = base_command()?;
    let template = format!(
        "download:{PROGRESS_TAG} %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s"
    );
    cmd.args([
        "-J",
        "--no-simulate",
        "--progress",
        "--newline",
        "--progress-template",
        &template,
    ])
    .args([
        "-f",
        "bestaudio/best",
        "--no-playlist",
        "--force-overwrites",
    ])
    .args(["-x", "--audio-format", "mp3", "--audio-quality", quality])
    .arg("-o")
    .arg(format!("{out_base}.%(ext)s"))
    .arg("--")
    .arg(url)
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| YouTubeError(format!("No se pudo ejecutar yt-dlp: {e}")))?;

    // El JSON sale por stdout; el progreso, según la versión, por stdout o stderr.
    let stderr = child.stderr.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    let err_thread = std::thread::spawn(move || {
        let mut all = String::new();
        for line in BufReader::new(stderr)
            .lines()
            .map_while(std::io::Result::ok)
        {
            if line.trim_start().starts_with(PROGRESS_TAG) {
                let _ = tx.send(line);
            } else {
                all.push_str(&line);
                all.push('\n');
            }
        }
        all
    });
    let mut json = String::new();
    let mut finished = false;
    let mut report = |line: &str, finished: &mut bool| {
        if let Some(p) = parse_progress(line) {
            progress(p, "Descargando");
            if p >= 0.9 && !*finished {
                *finished = true;
                progress(0.92, "Convirtiendo a MP3");
            }
        }
    };
    let mut out = BufReader::new(child.stdout.take().unwrap());
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match out.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let line = String::from_utf8_lossy(&buf);
        for l in rx.try_iter() {
            report(&l, &mut finished);
        }
        if line.trim_start().starts_with(PROGRESS_TAG) {
            report(&line, &mut finished);
        } else if line.trim_start().starts_with('{') {
            json = line.into_owned();
        }
    }
    let mut rest = Vec::new();
    let _ = out.read_to_end(&mut rest);
    let status = child.wait().map_err(|e| YouTubeError(e.to_string()))?;
    let stderr_text = err_thread.join().unwrap_or_default();
    for l in rx.try_iter() {
        report(&l, &mut finished);
    }
    if !status.success() {
        return err(clean_error(&stderr_text));
    }
    let info = serde_json::from_str(&json).unwrap_or(Value::Null);
    Ok((format!("{out_base}.mp3"), info))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn titles() {
        assert_eq!(
            clean_title("Rosalía - DESPECHÁ (Official Video)"),
            "Rosalía - DESPECHÁ"
        );
        assert_eq!(clean_title("Song [HD] [Lyrics]"), "Song");
        assert_eq!(clean_title("(Official Video)"), "(Official Video)");
        assert_eq!(clean_channel("Daft Punk - Topic"), "Daft Punk");
        assert_eq!(clean_channel("RihannaVEVO"), "Rihanna");
        assert_eq!(
            split_artist_title("Daft Punk – One More Time (Official Audio)", "x"),
            ("Daft Punk".into(), "One More Time".into())
        );
        assert_eq!(
            split_artist_title("One More Time", "Daft Punk - Topic"),
            ("Daft Punk".into(), "One More Time".into())
        );
    }

    #[test]
    fn errors() {
        assert_eq!(
            clean_error("WARNING: x\nERROR: [youtube] abc: Video unavailable\nmore"),
            "abc: Video unavailable"
        );
        assert_eq!(clean_error(""), "yt-dlp falló sin dar detalles");
    }

    #[test]
    fn similarity_matches_difflib() {
        // Valores de difflib.SequenceMatcher(None, a, b).ratio() en Python.
        assert!((similarity("one more time", "one more time") - 1.0).abs() < 1e-9);
        assert!((similarity("abcd", "bcde") - 0.75).abs() < 1e-9);
        assert!((similarity("hello", "world") - 0.2).abs() < 1e-9);
        assert!(
            (similarity("one more time (live)", "one more time") - 0.7878787878787878).abs() < 1e-9
        );
        assert!((similarity("despechá", "despecha") - 0.875).abs() < 1e-9);
        assert_eq!(similarity("", "abc"), 0.0);
    }

    fn search_json() -> Value {
        json!({"entries": [
            {"id": "live1", "ie_key": "Youtube", "title": "Daft Punk - One More Time (Live)", "channel": "Fan", "duration": 400},
            {"id": "chan", "ie_key": "YoutubeTab", "title": "Daft Punk"},
            {"id": "priv", "title": "[Private video]"},
            {"id": "topic", "ie_key": "Youtube", "title": "One More Time", "channel": "Daft Punk - Topic", "duration": 321.0},
            {"id": "vevo", "ie_key": "Youtube", "title": "Daft Punk - One More Time (Official Video)", "channel": "DaftPunkVEVO", "duration": 330},
            null
        ]})
    }

    #[test]
    fn search_parsing() {
        let tracks = parse_search(&search_json());
        assert_eq!(tracks.len(), 3);
        let t = &tracks[1];
        assert_eq!(
            (t.title.as_str(), t.artist.as_str()),
            ("One More Time", "Daft Punk")
        );
        assert_eq!(
            t.url.as_deref(),
            Some("https://www.youtube.com/watch?v=topic")
        );
        assert_eq!(
            t.thumb.as_deref(),
            Some("https://i.ytimg.com/vi/topic/mqdefault.jpg")
        );
        assert_eq!(t.duration, Some(321.0));
        assert_eq!(t.source, YOUTUBE);
    }

    #[test]
    fn matching_prefers_topic() {
        let mut target = Track::new("One More Time");
        target.artist = "Daft Punk".into();
        target.duration = Some(320.0);
        let best = best_match(&search_json(), &target).unwrap();
        assert_eq!(
            best.url.as_deref(),
            Some("https://www.youtube.com/watch?v=topic")
        );
    }

    #[test]
    fn playlist_parsing() {
        let info = json!({"title": "Mix", "channel": "Alguien - Topic",
            "thumbnails": [{"url": "a"}, {"url": "b"}],
            "entries": [{"id": "x", "title": "A - B", "duration": 10}]});
        let c = parse_playlist(&info, "https://youtube.com/playlist?list=PL1");
        assert_eq!(c.name, "Mix");
        assert_eq!(c.owner, "Alguien");
        assert_eq!(c.thumb.as_deref(), Some("b"));
        assert_eq!(c.tracks.len(), 1);
        let empty = parse_playlist(&json!({}), "u");
        assert_eq!(empty.name, "Playlist de YouTube");
        assert!(empty.thumb.is_none());
    }

    #[test]
    fn stream_parsing() {
        let info = json!({
            "id": "abc", "title": "T", "uploader": "U", "duration": 200.5,
            "requested_formats": [{"url": "https://rr1.googlevideo.com/videoplayback?expire=2000000000", "available_at": 123.0}],
            "http_headers": {"User-Agent": "UA", "Accept": "*/*"},
        });
        let st = parse_stream(&info, false).unwrap();
        assert!(st.source.input.contains("googlevideo"));
        assert_eq!(st.source.available_at, 123.0);
        assert_eq!(st.source.duration, Some(200.5));
        assert!(
            st.source
                .headers
                .contains(&("User-Agent".into(), "UA".into()))
        );
        assert_eq!(st.meta.channel.as_deref(), Some("U"));
        assert_eq!(expiry(&st.source.input), 2_000_000_000.0 - 600.0);
        assert!(parse_stream(&json!({"id": "x"}), true).is_err());
    }

    #[test]
    fn progress_lines() {
        assert_eq!(parse_progress("BSPROG 50 100 NA"), Some(0.45));
        assert_eq!(parse_progress("BSPROG 50 NA 200"), Some(0.225));
        assert_eq!(parse_progress("BSPROG 5 NA NA"), Some(0.05));
        assert_eq!(parse_progress("[download] 5%"), None);
    }

    #[test]
    fn cache_keys() {
        assert_eq!(cache_key("https://youtu.be/abc", true), "abc|fast");
        assert_eq!(
            cache_key("https://www.youtube.com/watch?v=abc", false),
            "abc|hq"
        );
    }
}
