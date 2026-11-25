//! YouTube y Spotify dentro de la app: búsqueda, enlaces y reproducción en streaming.
//!
//! Todo lo que toca la red corre en hilos; los resultados vuelven por un canal
//! (`Job`) que `App::tick` vacía en cada fotograma.

use std::sync::mpsc::Sender;
use std::thread;

use super::{App, View};
use crate::audio::Source;
use crate::models::{SPOTIFY, Track};
use crate::sources::{Collection, CollectionKind, Query, classify, spotify, youtube};

/// Resultado de un trabajo en segundo plano.
pub enum Job {
    /// Búsqueda, playlist o álbum para la vista de resultados. `quick` = poner el primero.
    Results(Result<Collection, String>, bool),
    /// Canciones sueltas (vídeo o canción de Spotify) que suenan o van a la cola.
    Enqueue(Result<Vec<Track>, String>),
    /// Stream resuelto para la pista `uid` de la cola (con la URL de YouTube emparejada).
    Stream {
        uid: String,
        result: Result<(Source, String), String>,
    },
}

/// Estado de lo online dentro de `App`.
#[derive(Default)]
pub struct Online {
    pub results: Option<Collection>,
    pub result_sel: usize,
    /// Mensaje mientras se busca, o el último error.
    pub results_status: Option<String>,
    /// Pista de la cola cuyo stream se está resolviendo.
    pub resolving: Option<String>,
    /// Fallos seguidos al resolver (para no saltar la cola entera en bucle).
    failures: usize,
}

fn spawn(tx: &Sender<Job>, work: impl FnOnce() -> Job + Send + 'static) {
    let tx = tx.clone();
    thread::spawn(move || {
        let _ = tx.send(work());
    });
}

/// URL de YouTube de una pista: la suya o, si viene de Spotify, la mejor coincidencia.
fn youtube_url(t: &Track) -> Result<String, String> {
    if let Some(u) = &t.url {
        return Ok(u.clone());
    }
    if t.source == SPOTIFY || t.spotify_uri.is_some() {
        return youtube::find_match(t)
            .map_err(|e| e.to_string())?
            .url
            .ok_or_else(|| "YouTube no dio un enlace".into());
    }
    Err("La pista no tiene archivo ni enlace".into())
}

/// Stream de arranque rápido si se puede; si no, el de alta calidad.
fn resolve(t: &Track) -> Result<(Source, String), String> {
    let url = youtube_url(t)?;
    if let Some(hq) = youtube::cached_stream(&url, false) {
        return Ok((hq.source, url));
    }
    let st = youtube::stream_info(&url, true)
        .or_else(|_| youtube::stream_info(&url, false))
        .map_err(|e| e.to_string())?;
    let mut src = st.source;
    if src.duration.is_none() {
        src.duration = t.duration;
    }
    Ok((src, url))
}

impl App {
    /// Texto de la barra que no es una ruta local.
    pub(super) fn submit_online(&mut self, text: &str) {
        let (quick, text) = match text.strip_prefix('+') {
            Some(rest) => (true, rest.trim()),
            None => (false, text),
        };
        let tx = self.job_tx.clone();
        match classify(text) {
            Query::Search(q) => {
                self.show_loading(format!("Buscando «{q}» en YouTube…"));
                spawn(&tx, move || {
                    let r = youtube::search(&q, 20).map(|tracks| Collection {
                        name: q.clone(),
                        tracks,
                        kind: CollectionKind::Search,
                        source: "youtube".into(),
                        ..Default::default()
                    });
                    Job::Results(r.map_err(|e| e.to_string()), quick)
                });
            }
            Query::YoutubePlaylist(url) => {
                self.show_loading("Leyendo la playlist de YouTube…".into());
                spawn(&tx, move || {
                    Job::Results(youtube::playlist(&url).map_err(|e| e.to_string()), false)
                });
            }
            Query::Spotify(reference) => {
                let (id, secret) = (
                    self.cfg.spotify_client_id.clone(),
                    self.cfg.spotify_client_secret.clone(),
                );
                let single = reference.starts_with("track:");
                if single {
                    self.notify("Leyendo Spotify…");
                } else {
                    self.show_loading("Leyendo Spotify…".into());
                }
                spawn(&tx, move || {
                    let r = spotify::fetch(&reference, &id, &secret).map_err(|e| e.to_string());
                    if single {
                        // Una canción suelta se comporta como un enlace de YouTube: suena o va a la cola.
                        Job::Enqueue(r.map(|c| c.tracks.into_iter().take(1).collect()))
                    } else {
                        Job::Results(r, false)
                    }
                });
            }
            Query::YoutubeVideo(url) | Query::Url(url) => {
                // vídeo de YouTube u otra web compatible con yt-dlp
                self.notify("Leyendo el enlace…");
                spawn(&tx, move || {
                    Job::Enqueue(
                        youtube::video_track(&url)
                            .map(|t| vec![t])
                            .map_err(|e| e.to_string()),
                    )
                });
            }
        }
    }

    fn show_loading(&mut self, msg: String) {
        self.online.results_status = Some(msg);
        self.online.results = None;
        self.online.result_sel = 0;
        self.view = View::Search;
    }

    /// Suena ya si no hay nada sonando; si no, a la cola.
    pub fn enqueue(&mut self, tracks: &[Track]) {
        if tracks.is_empty() {
            return;
        }
        if self.playing() || self.online.resolving.is_some() {
            self.queue.add(tracks);
            self.notify(match tracks {
                [t] => format!("En cola: {}", t.title),
                _ => format!("{} canciones añadidas a la cola", tracks.len()),
            });
        } else {
            let start = self.queue.len();
            self.queue.add(tracks);
            self.queue.jump(start);
            self.play_current();
        }
    }

    /// Empieza a resolver el stream de la pista actual (no local).
    pub(super) fn play_remote(&mut self, t: &Track) {
        if let Some(e) = self.engine.as_mut() {
            e.stop();
        }
        self.xfade_uid = None;
        self.online.resolving = Some(t.uid.clone());
        self.notify(format!("Cargando «{}»…", t.title));
        let track = t.clone();
        spawn(&self.job_tx, move || Job::Stream {
            uid: track.uid.clone(),
            result: resolve(&track),
        });
    }

    pub(super) fn poll_jobs(&mut self) {
        while let Ok(job) = self.job_rx.try_recv() {
            match job {
                Job::Results(Ok(col), quick) => self.show_results(col, quick),
                Job::Results(Err(e), _) => {
                    self.online.results_status = Some(format!("✖ {e}"));
                    self.notify(format!("No se pudo abrir: {e}"));
                }
                Job::Enqueue(Ok(tracks)) => self.enqueue(&tracks),
                Job::Enqueue(Err(e)) => self.notify(format!("No se pudo abrir: {e}")),
                Job::Stream { uid, result } => self.stream_ready(uid, result),
            }
        }
    }

    fn show_results(&mut self, col: Collection, quick: bool) {
        if col.tracks.is_empty() {
            self.online.results_status = Some("✖ No tiene canciones disponibles".into());
            return;
        }
        if col.source == SPOTIFY
            && col.tracks.len() >= spotify::EMBED_LIMIT
            && self.cfg.spotify_client_id.is_empty()
        {
            self.notify(
                "Spotify solo muestra las primeras 100 canciones sin credenciales de API. \
                 Mira el README para importar playlists completas.",
            );
        }
        if quick {
            self.enqueue(&col.tracks[..1]);
        }
        self.online.results_status = None;
        self.online.result_sel = 0;
        self.online.results = Some(col);
    }

    fn stream_ready(&mut self, uid: String, result: Result<(Source, String), String>) {
        if self.online.resolving.as_deref() != Some(uid.as_str()) {
            return; // el usuario ya pasó a otra canción
        }
        self.online.resolving = None;
        let current = self.queue.current().map(|t| t.uid.clone());
        if current.as_deref() != Some(uid.as_str()) {
            return;
        }
        match result {
            Ok((src, url)) => {
                self.online.failures = 0;
                // Recordar el emparejado (Spotify → YouTube) para no volver a buscarlo.
                if let Some(t) = self.queue.tracks.iter_mut().find(|t| t.uid == uid)
                    && t.url.is_none()
                {
                    t.url = Some(url);
                }
                match self.engine.as_mut() {
                    Some(e) => e.play(src, 0.0),
                    None => {
                        let msg = self.engine_error.clone().unwrap_or_default();
                        self.notify(msg);
                    }
                }
                self.status = None;
            }
            Err(e) => {
                let title = self
                    .queue
                    .current()
                    .map(|t| t.title.clone())
                    .unwrap_or_default();
                self.notify(format!("No se pudo reproducir «{title}»: {e}"));
                self.online.failures += 1;
                if self.online.failures < self.queue.len().min(5)
                    && self.queue.advance(false).is_some()
                {
                    self.play_current();
                } else {
                    self.online.failures = 0;
                }
            }
        }
    }

    // ---------------------------------------------------------------- vista de resultados
    pub fn result_tracks(&self) -> &[Track] {
        self.online
            .results
            .as_ref()
            .map(|c| c.tracks.as_slice())
            .unwrap_or(&[])
    }

    /// Enter: en una búsqueda suena (o va a la cola) esa canción; en una
    /// playlist o álbum se reproduce desde ahí.
    pub fn activate_result(&mut self) {
        let Some(col) = self.online.results.as_ref() else {
            return;
        };
        let sel = self
            .online
            .result_sel
            .min(col.tracks.len().saturating_sub(1));
        if col.tracks.is_empty() {
            return;
        }
        if col.kind == CollectionKind::Search {
            let t = col.tracks[sel].clone();
            self.enqueue(&[t]);
        } else {
            let tracks = col.tracks.clone();
            self.play_tracks(&tracks, sel);
            self.view = View::Queue;
        }
    }

    /// `a`: la seleccionada a la cola; `A` (all = true): todas.
    pub fn enqueue_results(&mut self, all: bool) {
        let tracks: Vec<Track> = if all {
            self.result_tracks().to_vec()
        } else {
            self.result_tracks()
                .get(self.online.result_sel)
                .cloned()
                .into_iter()
                .collect()
        };
        if tracks.is_empty() {
            return;
        }
        let n = tracks.len();
        self.queue.add(&tracks);
        self.notify(if n == 1 {
            "Añadida a la cola".to_string()
        } else {
            format!("{n} canciones añadidas a la cola")
        });
        if !self.playing() && self.online.resolving.is_none() && self.queue.current().is_none() {
            self.queue.jump(self.queue.len() - n);
            self.play_current();
        }
    }

    /// `i`: la seleccionada justo después de la actual.
    pub fn play_result_next(&mut self) {
        if let Some(t) = self.result_tracks().get(self.online.result_sel).cloned() {
            self.queue.insert_next(std::slice::from_ref(&t));
            self.notify(format!("A continuación: {}", t.title));
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::time::{Duration, Instant};

    /// Un `yt-dlp` falso que responde con JSON fijo: búsqueda o stream.
    fn fake_ytdlp(dir: &std::path::Path) -> std::path::PathBuf {
        let script = dir.join("yt-dlp");
        std::fs::write(
            &script,
            r#"#!/bin/sh
for a in "$@"; do last="$a"; done
case "$last" in
  ytsearch*) echo '{"entries":[{"id":"vid1","ie_key":"Youtube","title":"Artista - Canción (Official Video)","channel":"ArtistaVEVO","duration":200}]}' ;;
  *vid1*) echo '{"id":"vid1","title":"Artista - Canción","duration":200,"url":"https://example.invalid/audio?expire=4000000000","http_headers":{"User-Agent":"UA"}}' ;;
  *) echo "ERROR: [youtube] x: Video unavailable" >&2; exit 1 ;;
esac
"#,
        )
        .unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        script
    }

    fn wait_for(app: &mut App, done: impl Fn(&App) -> bool) {
        let t0 = Instant::now();
        while !done(app) && t0.elapsed() < Duration::from_secs(10) {
            app.poll_jobs();
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(done(app), "tiempo de espera agotado");
    }

    #[test]
    fn search_and_resolve_with_fake_ytdlp() {
        let dir = std::env::temp_dir().join(format!("beatshell-online-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let exe = fake_ytdlp(&dir);
        // SAFETY: solo esta prueba toca estas variables, antes de leerlas.
        unsafe {
            std::env::set_var("BEATSHELL_YTDLP", &exe);
            std::env::set_var("BEATSHELL_HOME", &dir);
        }
        let mut app = App::new();
        app.submit("rosalía");
        assert_eq!(app.view, View::Search);
        wait_for(&mut app, |a| a.online.results.is_some());
        let t = app.result_tracks()[0].clone();
        assert_eq!(
            (t.artist.as_str(), t.title.as_str()),
            ("Artista", "Canción")
        );

        // Enter: se resuelve el stream (el resultado llega por el canal).
        let (tx, rx) = std::sync::mpsc::channel();
        app.job_tx = tx;
        app.activate_result();
        assert!(app.online.resolving.is_some());
        let Job::Stream { result, .. } = rx.recv_timeout(Duration::from_secs(10)).unwrap() else {
            panic!("se esperaba un stream")
        };
        let (src, url) = result.unwrap();
        assert_eq!(url, "https://www.youtube.com/watch?v=vid1");
        assert!(src.input.starts_with("https://example.invalid/audio"));
        assert_eq!(
            src.headers,
            vec![("User-Agent".to_string(), "UA".to_string())]
        );

        // Los errores de yt-dlp llegan limpios.
        let err = youtube::stream_info("https://www.youtube.com/watch?v=zzz", false).unwrap_err();
        assert_eq!(err.to_string(), "x: Video unavailable");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
