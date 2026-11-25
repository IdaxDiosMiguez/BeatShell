//! Estado de la aplicación y lógica de reproducción (independiente del dibujo).

use std::path::Path;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant};

use crate::audio::{AudioEngine, Source, Spectrum};
use crate::config::Config;
use crate::eq;
use crate::library::{self, Library, PlaylistStore};
use crate::models::Track;
use crate::playqueue::{PlayQueue, Repeat};

mod online;
use online::Job;
pub use online::Online;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Queue,
    /// Resultados de YouTube/Spotify.
    Search,
    Library,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Focus {
    Sources,
    Tracks,
}

/// Una entrada del panel izquierdo de la biblioteca.
#[derive(Debug, Clone)]
pub enum LibSource {
    AllLocal,
    Folder(String),
    Playlist(String),
}

pub struct App {
    pub cfg: Config,
    pub queue: PlayQueue,
    pub library: Library,
    pub playlists: PlaylistStore,
    pub engine: Option<AudioEngine>,
    pub engine_error: Option<String>,

    pub view: View,
    pub focus: Focus,
    pub queue_sel: usize,
    pub source_sel: usize,
    pub track_sel: usize,
    /// Pistas de la fuente seleccionada en la biblioteca.
    pub lib_tracks: Vec<Track>,

    /// Barra de entrada activa (`/`).
    pub input: Option<String>,
    status: Option<(String, Instant)>,
    pub scanning: bool,
    scan_rx: Option<Receiver<(Library, usize)>>,

    spectrum: Spectrum,
    viz_buf: Vec<f32>,
    pub bands: Vec<f32>,
    muted_from: Option<u8>,
    xfade_uid: Option<String>,
    saved_version: u64,
    last_save: Instant,
    pub quit: bool,

    /// YouTube y Spotify (ver `app/online.rs`).
    pub online: Online,
    job_tx: Sender<Job>,
    job_rx: Receiver<Job>,
}

impl App {
    pub fn new() -> App {
        let cfg = Config::load();
        let (engine, engine_error) = match AudioEngine::new(cfg.volume as u8) {
            Ok(mut e) => {
                e.set_filters(&eq::filter_chain(&cfg.eq_preset, cfg.dj_mode));
                (Some(e), None)
            }
            Err(e) => (None, Some(format!("Sin audio: {e:#}"))),
        };
        let mut queue = PlayQueue::new();
        let (tracks, index) = library::load_session();
        if !tracks.is_empty() {
            queue.index = index.clamp(-1, tracks.len() as isize - 1);
            queue.tracks = tracks;
        }
        queue.repeat = Repeat::parse(&cfg.repeat);
        queue.set_shuffle(cfg.shuffle);
        let (job_tx, job_rx) = channel();
        let mut app = App {
            library: Library::load(),
            playlists: PlaylistStore::load(),
            cfg,
            queue_sel: queue.index.max(0) as usize,
            saved_version: queue.version,
            queue,
            engine,
            engine_error,
            view: View::Queue,
            focus: Focus::Sources,
            source_sel: 0,
            track_sel: 0,
            lib_tracks: Vec::new(),
            input: None,
            status: None,
            scanning: false,
            scan_rx: None,
            spectrum: Spectrum::new(),
            viz_buf: Vec::new(),
            bands: Vec::new(),
            muted_from: None,
            xfade_uid: None,
            last_save: Instant::now(),
            quit: false,
            online: Online::default(),
            job_tx,
            job_rx,
        };
        if app.queue.is_empty() && app.library.index.is_empty() && app.playlists.items.is_empty() {
            app.notify(
                "Pulsa / y busca en YouTube, pega un enlace o la ruta de tu carpeta de música",
            );
        }
        app.refresh_lib_tracks();
        app
    }

    // ---------------------------------------------------------------- estado
    pub fn notify(&mut self, msg: impl Into<String>) {
        self.status = Some((msg.into(), Instant::now()));
    }

    pub fn status(&self) -> Option<&str> {
        match &self.status {
            Some((m, t)) if t.elapsed() < Duration::from_secs(6) => Some(m),
            _ => None,
        }
    }

    pub fn position(&self) -> f64 {
        self.engine.as_ref().map_or(0.0, |e| e.position())
    }

    pub fn duration(&self) -> Option<f64> {
        self.engine
            .as_ref()
            .and_then(|e| e.duration())
            .or_else(|| self.queue.current().and_then(|t| t.duration))
    }

    pub fn playing(&self) -> bool {
        self.engine.as_ref().is_some_and(|e| e.active())
    }

    pub fn paused(&self) -> bool {
        self.engine.as_ref().is_some_and(|e| e.paused())
    }

    pub fn buffering(&self) -> bool {
        self.online.resolving.is_some() || self.engine.as_ref().is_some_and(|e| e.buffering())
    }

    pub fn volume(&self) -> u8 {
        self.cfg.volume as u8
    }

    // ------------------------------------------------------------ biblioteca
    pub fn sources(&self) -> Vec<(LibSource, String)> {
        let mut v = vec![(
            LibSource::AllLocal,
            format!("♫ Archivos locales ({})", self.library.index.len()),
        )];
        for f in &self.library.folders {
            v.push((LibSource::Folder(f.id.clone()), format!("  ▸ {}", f.name)));
        }
        for p in self.playlists.playable() {
            v.push((
                LibSource::Playlist(p.id.clone()),
                format!(
                    "{} {} ({})",
                    library::kind_icon(&p.kind),
                    p.name,
                    p.tracks.len()
                ),
            ));
        }
        v
    }

    pub fn refresh_lib_tracks(&mut self) {
        let sources = self.sources();
        self.source_sel = self.source_sel.min(sources.len().saturating_sub(1));
        self.lib_tracks = match sources.get(self.source_sel).map(|s| &s.0) {
            Some(LibSource::AllLocal) | None => self.library.tracks(None),
            Some(LibSource::Folder(id)) => self.library.tracks(Some(id)),
            Some(LibSource::Playlist(id)) => self
                .playlists
                .items
                .iter()
                .find(|p| &p.id == id)
                .map(|p| p.tracks.clone())
                .unwrap_or_default(),
        };
        self.track_sel = self.track_sel.min(self.lib_tracks.len().saturating_sub(1));
    }

    fn start_scan(&mut self, add: Option<String>) {
        if self.scanning {
            self.notify("Ya se está escaneando la biblioteca…");
            return;
        }
        let (tx, rx) = channel();
        self.scanning = true;
        self.scan_rx = Some(rx);
        std::thread::spawn(move || {
            let mut lib = Library::load();
            if let Some(p) = add {
                lib.add_folder(&p);
            }
            let n = lib.scan();
            let _ = tx.send((lib, n));
        });
        self.notify("Escaneando la biblioteca…");
    }

    pub fn rescan(&mut self) {
        if self.library.folders.is_empty() {
            self.notify("No hay carpetas en la biblioteca: pulsa / y escribe una ruta");
            return;
        }
        self.start_scan(None);
    }

    // ------------------------------------------------------------ reproducir
    /// Reproduce la pista actual de la cola; si no es local, salta a la siguiente que sí lo sea.
    pub fn play_current(&mut self) {
        let mut tries = self.queue.len();
        loop {
            let Some(t) = self.queue.current().cloned() else {
                return;
            };
            if t.is_local() {
                self.xfade_uid = None;
                self.online.resolving = None;
                match self.engine.as_mut() {
                    Some(e) => e.play(
                        Source::file(t.path.as_deref().unwrap_or(""), t.duration),
                        0.0,
                    ),
                    None => {
                        let msg = self.engine_error.clone().unwrap_or_default();
                        self.notify(msg);
                    }
                }
                self.queue_sel = self.queue.index.max(0) as usize;
                return;
            }
            if t.url.is_some() || t.spotify_uri.is_some() {
                self.play_remote(&t);
                self.queue_sel = self.queue.index.max(0) as usize;
                return;
            }
            self.notify(format!("«{}» ya no está en el disco", t.title));
            tries = tries.saturating_sub(1);
            if tries == 0 || self.queue.advance(false).is_none() {
                if let Some(e) = self.engine.as_mut() {
                    e.stop();
                }
                return;
            }
        }
    }

    pub fn play_tracks(&mut self, tracks: &[Track], start: usize) {
        if tracks.is_empty() {
            return;
        }
        self.queue.replace(tracks, start);
        self.play_current();
    }

    pub fn toggle_pause(&mut self) {
        if !self.playing() {
            if self.queue.current().is_some() {
                self.play_current();
            } else if !self.queue.is_empty() {
                self.queue.jump(0);
                self.play_current();
            }
            return;
        }
        if let Some(e) = self.engine.as_mut() {
            e.toggle_pause();
        }
    }

    pub fn next(&mut self) {
        if self.queue.advance(false).is_some() {
            self.play_current();
        } else {
            self.notify("No hay más canciones en la cola");
        }
    }

    pub fn prev(&mut self) {
        // Como en cualquier reproductor: si llevamos un rato, vuelve al principio.
        if self.position() > 3.0 {
            if let Some(e) = self.engine.as_mut() {
                e.seek(0.0);
            }
            return;
        }
        if self.queue.back().is_some() {
            self.play_current();
        }
    }

    pub fn seek(&mut self, delta: f64) {
        if let Some(e) = self.engine.as_mut() {
            e.seek_relative(delta);
        }
    }

    pub fn change_volume(&mut self, delta: i64) {
        self.muted_from = None;
        self.set_volume((self.cfg.volume + delta).clamp(0, 100) as u8);
        self.save_config();
    }

    fn set_volume(&mut self, v: u8) {
        self.cfg.volume = v as i64;
        if let Some(e) = self.engine.as_mut() {
            e.set_volume(v);
        }
    }

    pub fn toggle_mute(&mut self) {
        match self.muted_from.take() {
            Some(v) => self.set_volume(v),
            None => {
                self.muted_from = Some(self.volume());
                self.set_volume(0);
            }
        }
    }

    pub fn toggle_shuffle(&mut self) {
        let on = !self.queue.shuffle();
        self.queue.set_shuffle(on);
        self.cfg.shuffle = on;
        self.save_config();
        self.notify(if on {
            "Aleatorio activado"
        } else {
            "Aleatorio desactivado"
        });
    }

    pub fn cycle_repeat(&mut self) {
        let r = self.queue.cycle_repeat();
        self.cfg.repeat = r.as_str().into();
        self.save_config();
        self.notify(match r {
            Repeat::Off => "Repetir: no",
            Repeat::All => "Repetir: toda la cola",
            Repeat::One => "Repetir: esta canción",
        });
    }

    fn apply_filters(&mut self) {
        let chain = eq::filter_chain(&self.cfg.eq_preset, self.cfg.dj_mode);
        if let Some(e) = self.engine.as_mut() {
            e.set_filters(&chain);
        }
    }

    pub fn cycle_eq(&mut self) {
        self.cfg.eq_preset = eq::next_preset(&self.cfg.eq_preset).into();
        self.apply_filters();
        self.save_config();
        self.notify(format!("Ecualizador: {}", self.cfg.eq_preset));
    }

    pub fn toggle_dj(&mut self) {
        self.cfg.dj_mode = !self.cfg.dj_mode;
        self.apply_filters();
        self.save_config();
        self.notify(if self.cfg.dj_mode {
            format!(
                "Modo DJ: fundidos de {:.0} s y volumen nivelado",
                self.cfg.dj_crossfade
            )
        } else {
            "Modo DJ desactivado".into()
        });
    }

    fn save_config(&mut self) {
        if let Err(e) = self.cfg.save() {
            self.notify(format!("No se pudo guardar la configuración: {e}"));
        }
    }

    pub fn save_session(&mut self) {
        let _ = library::save_session(&self.queue.tracks, self.queue.index);
        self.saved_version = self.queue.version;
        self.last_save = Instant::now();
    }

    // ------------------------------------------------------------- acciones
    pub fn activate_queue(&mut self) {
        if self.queue_sel < self.queue.len() {
            self.queue.jump(self.queue_sel);
            self.play_current();
        }
    }

    pub fn remove_from_queue(&mut self) {
        if self.queue_sel >= self.queue.len() {
            return;
        }
        let was_current = self.queue.index == self.queue_sel as isize;
        self.queue.remove(self.queue_sel);
        if was_current && let Some(e) = self.engine.as_mut() {
            e.stop();
        }
        self.queue_sel = self.queue_sel.min(self.queue.len().saturating_sub(1));
    }

    pub fn move_in_queue(&mut self, delta: isize) {
        self.queue_sel = self.queue.move_item(self.queue_sel, delta);
    }

    pub fn activate_library(&mut self) {
        match self.focus {
            Focus::Sources => {
                let tracks = self.lib_tracks.clone();
                self.play_tracks(&tracks, 0);
            }
            Focus::Tracks => {
                let tracks = self.lib_tracks.clone();
                self.play_tracks(&tracks, self.track_sel);
            }
        }
    }

    pub fn enqueue_selection(&mut self) {
        let tracks: Vec<Track> = match self.focus {
            Focus::Sources => self.lib_tracks.clone(),
            Focus::Tracks => self
                .lib_tracks
                .get(self.track_sel)
                .cloned()
                .into_iter()
                .collect(),
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
        if !self.playing() && self.queue.current().is_none() {
            self.queue.jump(self.queue.len() - n);
            self.play_current();
        }
    }

    /// Lo que se escribe en la barra de arriba.
    pub fn submit(&mut self, text: &str) {
        let text = clean_path(text);
        if text.is_empty() {
            return;
        }
        let path = library::resolve(&text);
        if path.is_dir() {
            self.start_scan(Some(path.to_string_lossy().into_owned()));
            self.view = View::Library;
        } else if path.is_file() && library::is_playlist_file(&path) {
            let tracks = library::read_m3u(&path);
            if tracks.is_empty() {
                self.notify("La playlist está vacía o sus archivos no existen");
            } else {
                self.notify(format!(
                    "{} canciones de {}",
                    tracks.len(),
                    file_name(&path)
                ));
                self.play_tracks(&tracks, 0);
                self.view = View::Queue;
            }
        } else if path.is_file() && library::is_audio(&path) {
            let t = library::track_from_file(&path);
            if self.playing() {
                self.queue.add(std::slice::from_ref(&t));
                self.notify(format!("En cola: {}", t.title));
            } else {
                self.play_tracks(&[t], 0);
            }
        } else if path.exists() {
            self.notify("Ese archivo no es de audio");
        } else {
            self.submit_online(&text);
        }
    }

    // ---------------------------------------------------------------- tick
    pub fn tick(&mut self, viz_bands: usize) {
        self.poll_jobs();
        if let Some(rx) = &self.scan_rx
            && let Ok((lib, n)) = rx.try_recv()
        {
            self.library = lib;
            self.scanning = false;
            self.scan_rx = None;
            self.refresh_lib_tracks();
            self.notify(format!("Biblioteca lista: {n} canciones"));
        }
        let Some(engine) = self.engine.as_mut() else {
            return;
        };
        engine.ensure_stream();

        if engine.poll_finished() {
            if let Some(err) = engine.error() {
                let title = self
                    .queue
                    .current()
                    .map(|t| t.title.clone())
                    .unwrap_or_default();
                self.notify(format!("No se pudo reproducir «{title}»: {err}"));
            }
            if self.queue.advance(true).is_some() {
                self.play_current();
            }
        } else if self.cfg.dj_mode {
            self.maybe_crossfade();
        }

        // Visualizador con caída suave.
        if let Some(engine) = self.engine.as_ref() {
            engine.viz_samples(&mut self.viz_buf);
            let rate = engine.samplerate;
            let target = if engine.paused() || !engine.active() {
                vec![0.0; viz_bands]
            } else {
                self.spectrum.bands(&self.viz_buf, rate, viz_bands)
            };
            if self.bands.len() != viz_bands {
                self.bands = vec![0.0; viz_bands];
            }
            for (b, t) in self.bands.iter_mut().zip(target) {
                *b = if t > *b { t } else { (*b - 0.06).max(t) };
            }
        }

        if self.queue.version != self.saved_version
            && self.last_save.elapsed() > Duration::from_secs(3)
        {
            self.save_session();
        }
    }

    /// Modo DJ: empieza la siguiente antes de que acabe la actual.
    fn maybe_crossfade(&mut self) {
        let Some(engine) = self.engine.as_ref() else {
            return;
        };
        if engine.crossfading() || engine.paused() || !engine.active() {
            return;
        }
        let Some(dur) = engine.duration() else { return };
        let fade = self.cfg.dj_crossfade.clamp(1.0, 20.0).min(dur / 3.0);
        if engine.position() < dur - fade {
            return;
        }
        let cur_uid = self.queue.current().map(|t| t.uid.clone());
        if cur_uid.is_none() || self.xfade_uid == cur_uid {
            return;
        }
        let Some(next) = self.queue.peek_next().cloned() else {
            return;
        };
        if !next.is_local() {
            return;
        }
        self.queue.advance(true);
        self.xfade_uid = self.queue.current().map(|t| t.uid.clone());
        self.queue_sel = self.queue.index.max(0) as usize;
        if let Some(e) = self.engine.as_mut() {
            e.crossfade_to(
                Source::file(next.path.as_deref().unwrap_or(""), next.duration),
                fade,
            );
        }
    }
}

fn file_name(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Rutas arrastradas a la terminal: comillas, `file://` y espacios escapados.
pub fn clean_path(text: &str) -> String {
    let mut s = text.trim().to_string();
    for q in ['\'', '"'] {
        if s.len() >= 2 && s.starts_with(q) && s.ends_with(q) {
            s = s[1..s.len() - 1].to_string();
        }
    }
    if let Some(rest) = s.strip_prefix("file://") {
        s = rest.replace("%20", " ");
    }
    if !cfg!(windows) && s.contains("\\ ") {
        s = s.replace("\\ ", " ");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::clean_path;

    #[test]
    fn dragged_paths() {
        assert_eq!(clean_path("  '/home/a/Mi Música'  "), "/home/a/Mi Música");
        assert_eq!(clean_path("\"C:\\Music\""), "C:\\Music");
        assert_eq!(clean_path("file:///m/a%20b.mp3"), "/m/a b.mp3");
        if !cfg!(windows) {
            assert_eq!(clean_path("/m/Mi\\ Música"), "/m/Mi Música");
        }
    }
}
