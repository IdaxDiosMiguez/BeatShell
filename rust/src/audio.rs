//! Motor de audio multiplataforma.
//!
//! ffmpeg decodifica cualquier fuente (archivo local o stream HTTP) a PCM f32
//! estéreo y cpal lo envía a la tarjeta de sonido. Tener las muestras en nuestras
//! manos permite un visualizador de espectro real, control de volumen sin clics y
//! fundidos entre canciones (modo DJ), igual en Windows, macOS y Linux.
//!
//! El motor maneja hasta dos "voces": la canción principal y, durante un fundido
//! cruzado, la que se está despidiendo.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, anyhow};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, SampleFormat, SizedSample};

#[cfg(test)]
use crate::eq;
use crate::ffmpeg;

pub const CHANNELS: usize = 2;
const BYTES_PER_FRAME: usize = 4 * CHANNELS;
const READ_FRAMES: usize = 4096;
const MAX_BUFFER_SECONDS: f64 = 12.0;
const PREBUFFER_SECONDS: f64 = 0.2;
pub const VIZ_SIZE: usize = 2048;
/// Fundido mínimo al empezar o saltar: evita clics.
const CLICK_FADE: f64 = 0.04;

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Algo que ffmpeg sabe abrir.
#[derive(Debug, Clone, Default)]
pub struct Source {
    pub input: String,
    pub headers: Vec<(String, String)>,
    pub duration: Option<f64>,
    /// Timestamp unix a partir del cual se puede empezar (YouTube, fase 2).
    pub available_at: f64,
}

impl Source {
    pub fn file(path: &str, duration: Option<f64>) -> Source {
        Source {
            input: path.to_string(),
            duration,
            ..Default::default()
        }
    }

    pub fn is_remote(&self) -> bool {
        self.input.starts_with("http://") || self.input.starts_with("https://")
    }
}

/// Una canción decodificándose: su búfer, su posición y su envolvente de volumen.
struct Voice {
    id: u64,
    source: Arc<Source>,
    /// Cambia en cada reinicio (seek, cambio de filtros).
    token: u64,
    /// Bloques de muestras intercaladas L,R.
    chunks: VecDeque<Vec<f32>>,
    /// Frames ya consumidos del primer bloque.
    offset: usize,
    buffered: usize,
    base: f64,
    played: u64,
    ready: bool,
    decoder_done: bool,
    error: Option<String>,
    g0: f32,
    g1: f32,
    fade_len: u64,
    fade_pos: u64,
}

impl Voice {
    fn new(id: u64, source: Source) -> Voice {
        Voice {
            id,
            source: Arc::new(source),
            token: 0,
            chunks: VecDeque::new(),
            offset: 0,
            buffered: 0,
            base: 0.0,
            played: 0,
            ready: false,
            decoder_done: false,
            error: None,
            g0: 0.0,
            g1: 1.0,
            fade_len: 1,
            fade_pos: 0,
        }
    }

    fn reset(&mut self, start: f64) -> u64 {
        self.token += 1;
        self.chunks.clear();
        self.offset = 0;
        self.buffered = 0;
        self.base = start;
        self.played = 0;
        self.ready = false;
        self.decoder_done = false;
        self.error = None;
        self.token
    }

    fn set_fade(&mut self, g0: f32, g1: f32, frames: u64) {
        self.g0 = g0;
        self.g1 = g1;
        self.fade_len = frames.max(1);
        self.fade_pos = 0;
    }

    fn fade_done(&self) -> bool {
        self.fade_pos >= self.fade_len
    }

    /// Ganancia del frame `i` contando desde la posición actual del fundido.
    /// Curva de igual potencia: el volumen percibido se mantiene.
    fn gain_at(&self, i: u64) -> f32 {
        let pos = self.fade_pos + i;
        if pos >= self.fade_len {
            return self.g1;
        }
        let t = pos as f32 / self.fade_len as f32;
        let half_pi = std::f32::consts::FRAC_PI_2;
        if self.g1 > self.g0 {
            self.g0 + (self.g1 - self.g0) * (t * half_pi).sin()
        } else {
            self.g1 + (self.g0 - self.g1) * (t * half_pi).cos()
        }
    }

    /// Mezcla hasta `frames` frames en `mix` aplicando la envolvente. Devuelve cuántos.
    fn mix_into(&mut self, mix: &mut [f32], advance_fade: bool) -> usize {
        let frames = mix.len() / CHANNELS;
        let mut pos = 0;
        while pos < frames {
            let Some(chunk) = self.chunks.front() else {
                break;
            };
            let chunk_frames = chunk.len() / CHANNELS;
            let n = (chunk_frames - self.offset).min(frames - pos);
            for f in 0..n {
                let g = self.gain_at((pos + f) as u64);
                for c in 0..CHANNELS {
                    mix[(pos + f) * CHANNELS + c] += chunk[(self.offset + f) * CHANNELS + c] * g;
                }
            }
            pos += n;
            self.offset += n;
            if self.offset >= chunk_frames {
                self.chunks.pop_front();
                self.offset = 0;
            }
        }
        if advance_fade && !self.fade_done() {
            self.fade_pos += pos as u64;
        }
        self.buffered = self.buffered.saturating_sub(pos);
        self.played += pos as u64;
        pos
    }

    fn exhausted(&self) -> bool {
        self.decoder_done && self.buffered == 0
    }
}

struct State {
    main: Option<Voice>,
    /// La que se despide durante un fundido.
    out: Option<Voice>,
    paused: bool,
    finished: bool,
    last_error: Option<String>,
    filters: String,
    gain: f32,
    target_gain: f32,
    viz: VecDeque<f32>,
    next_id: u64,
}

impl State {
    fn voice_mut(&mut self, id: u64) -> Option<&mut Voice> {
        if self.main.as_ref().is_some_and(|v| v.id == id) {
            return self.main.as_mut();
        }
        if self.out.as_ref().is_some_and(|v| v.id == id) {
            return self.out.as_mut();
        }
        None
    }

    /// Mezcla las voces en `mix` (intercalado estéreo). Devuelve los frames producidos.
    fn render(&mut self, mix: &mut [f32]) -> usize {
        let mut produced = 0;
        let main_ready = self.main.as_ref().is_some_and(|v| v.ready);
        if main_ready && let Some(m) = self.main.as_mut() {
            produced = m.mix_into(mix, true);
        }
        if let Some(out) = self.out.as_mut() {
            // el fundido de salida avanza solo cuando la nueva ya suena
            let n = out.mix_into(mix, main_ready);
            produced = produced.max(n);
            if out.exhausted() || (main_ready && out.fade_done()) {
                self.out = None;
            }
        }
        if main_ready {
            let m = self.main.as_mut().expect("main");
            if m.exhausted() {
                // Terminó (o falló): lo avisamos una vez y liberamos la voz.
                self.last_error = m.error.take();
                self.main = None;
                self.finished = true;
            } else if m.buffered == 0 && !m.decoder_done {
                m.ready = false; // nos quedamos sin datos: volver a pre-cargar
            }
        }
        produced
    }
}

type Shared = Arc<(Mutex<State>, Condvar)>;

fn lock(shared: &Shared) -> MutexGuard<'_, State> {
    shared.0.lock().unwrap_or_else(|e| e.into_inner())
}

pub struct AudioEngine {
    shared: Shared,
    stream: Option<cpal::Stream>,
    stream_failed: Arc<AtomicBool>,
    pub samplerate: u32,
    volume: u8,
}

impl AudioEngine {
    pub fn new(volume: u8) -> Result<AudioEngine> {
        let shared: Shared = Arc::new((
            Mutex::new(State {
                main: None,
                out: None,
                paused: false,
                finished: false,
                last_error: None,
                filters: String::new(),
                gain: 0.0,
                target_gain: 0.0,
                viz: VecDeque::from(vec![0.0; VIZ_SIZE]),
                next_id: 1,
            }),
            Condvar::new(),
        ));
        let mut eng = AudioEngine {
            shared,
            stream: None,
            stream_failed: Arc::new(AtomicBool::new(false)),
            samplerate: 48000,
            volume: 0,
        };
        eng.set_volume(volume);
        eng.open_stream()?;
        Ok(eng)
    }

    // ------------------------------------------------------------ dispositivo
    fn open_stream(&mut self) -> Result<()> {
        self.stream = None;
        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .ok_or_else(|| anyhow!("No hay dispositivo de salida de audio"))?;
        let supported = device
            .default_output_config()
            .context("No se pudo leer la configuración de audio")?;
        let format = supported.sample_format();
        let config: cpal::StreamConfig = supported.into();
        let rate = config.sample_rate;
        self.samplerate = if (8000..=192_000).contains(&rate) {
            rate
        } else {
            48000
        };
        let stream = match format {
            SampleFormat::F32 => self.build::<f32>(&device, config),
            SampleFormat::F64 => self.build::<f64>(&device, config),
            SampleFormat::I16 => self.build::<i16>(&device, config),
            SampleFormat::I32 => self.build::<i32>(&device, config),
            SampleFormat::U16 => self.build::<u16>(&device, config),
            SampleFormat::U8 => self.build::<u8>(&device, config),
            SampleFormat::I8 => self.build::<i8>(&device, config),
            other => Err(anyhow!("Formato de audio no soportado: {other}")),
        }?;
        stream
            .play()
            .context("No se pudo iniciar la salida de audio")?;
        self.stream_failed.store(false, Ordering::SeqCst);
        self.stream = Some(stream);
        Ok(())
    }

    fn build<T>(&self, device: &cpal::Device, config: cpal::StreamConfig) -> Result<cpal::Stream>
    where
        T: SizedSample + FromSample<f32> + Send + 'static,
    {
        let channels = config.channels as usize;
        let shared = self.shared.clone();
        let failed = self.stream_failed.clone();
        let mut mix: Vec<f32> = Vec::new();
        let stream = device.build_output_stream(
            config,
            move |data: &mut [T], _: &cpal::OutputCallbackInfo| {
                let frames = data.len() / channels.max(1);
                mix.clear();
                mix.resize(frames * CHANNELS, 0.0);
                callback(&shared, &mut mix);
                for (f, out) in data.chunks_mut(channels).enumerate() {
                    let (l, r) = (mix[f * 2], mix[f * 2 + 1]);
                    match out.len() {
                        1 => out[0] = T::from_sample((l + r) * 0.5),
                        _ => {
                            out[0] = T::from_sample(l);
                            out[1] = T::from_sample(r);
                            for s in &mut out[2..] {
                                *s = T::from_sample(0.0f32);
                            }
                        }
                    }
                }
            },
            move |_err| failed.store(true, Ordering::SeqCst),
            None,
        )?;
        Ok(stream)
    }

    /// Reabre la salida si el dispositivo cambió o el stream murió.
    pub fn ensure_stream(&mut self) {
        if self.stream.is_some() && !self.stream_failed.load(Ordering::SeqCst) {
            return;
        }
        let pos = self.position();
        let old_rate = self.samplerate;
        if self.open_stream().is_ok() && old_rate != self.samplerate && self.active() {
            self.seek(pos);
        }
    }

    // ---------------------------------------------------------------- volumen
    pub fn volume(&self) -> u8 {
        self.volume
    }

    pub fn set_volume(&mut self, v: u8) {
        self.volume = v.min(100);
        // Curva perceptual: el oído percibe el volumen de forma logarítmica.
        let g = (self.volume as f32 / 100.0).powi(2);
        lock(&self.shared).target_gain = g;
    }

    // ---------------------------------------------------------------- filtros
    /// Cambia la cadena de filtros de ffmpeg (EQ, nivelado) al vuelo.
    pub fn set_filters(&mut self, chain: &str) {
        let restart = {
            let mut st = lock(&self.shared);
            if st.filters == chain {
                return;
            }
            st.filters = chain.to_string();
            st.main.as_ref().filter(|v| !v.exhausted()).map(|v| v.id)
        };
        if let Some(id) = restart {
            // Se reinicia en el punto actual: el cambio se oye ya.
            self.restart(id, self.position(), CLICK_FADE);
        }
    }

    // ------------------------------------------------------------- transporte
    pub fn play(&mut self, source: Source, start: f64) {
        self.ensure_stream();
        let id = {
            let mut st = lock(&self.shared);
            st.out = None;
            let id = st.next_id;
            st.next_id += 1;
            st.main = Some(Voice::new(id, source));
            st.paused = false;
            st.finished = false;
            st.last_error = None;
            self.shared.1.notify_all();
            id
        };
        self.restart(id, start.max(0.0), CLICK_FADE);
    }

    /// Empieza `source` mientras la actual se desvanece (modo DJ).
    pub fn crossfade_to(&mut self, source: Source, seconds: f64) {
        let can = {
            let st = lock(&self.shared);
            st.main.is_some() && !st.paused && seconds > 0.0
        };
        if !can {
            self.play(source, 0.0);
            return;
        }
        self.ensure_stream();
        let frames = (seconds * self.samplerate as f64) as u64;
        let id = {
            let mut st = lock(&self.shared);
            let mut old = st.main.take().expect("main");
            let from = if old.fade_done() { old.g1 } else { 1.0 };
            old.set_fade(from, 0.0, frames);
            st.out = Some(old);
            let id = st.next_id;
            st.next_id += 1;
            st.main = Some(Voice::new(id, source));
            st.finished = false;
            id
        };
        self.restart(id, 0.0, seconds);
    }

    pub fn seek(&mut self, seconds: f64) {
        let Some((id, dur)) = lock(&self.shared)
            .main
            .as_ref()
            .map(|v| (v.id, v.source.duration))
        else {
            return;
        };
        let mut s = seconds;
        if let Some(d) = dur {
            s = s.min((d - 0.5).max(0.0));
        }
        self.restart(id, s.max(0.0), CLICK_FADE);
    }

    pub fn seek_relative(&mut self, delta: f64) {
        self.seek(self.position() + delta);
    }

    pub fn toggle_pause(&mut self) -> bool {
        let mut st = lock(&self.shared);
        if st.main.is_some() {
            st.paused = !st.paused;
        }
        st.paused
    }

    pub fn stop(&mut self) {
        let mut st = lock(&self.shared);
        st.main = None;
        st.out = None;
        st.viz.iter_mut().for_each(|x| *x = 0.0);
        self.shared.1.notify_all();
    }

    // ----------------------------------------------------------------- estado
    pub fn position(&self) -> f64 {
        let st = lock(&self.shared);
        st.main
            .as_ref()
            .map_or(0.0, |v| v.base + v.played as f64 / self.samplerate as f64)
    }

    pub fn duration(&self) -> Option<f64> {
        lock(&self.shared)
            .main
            .as_ref()
            .and_then(|v| v.source.duration)
    }

    pub fn paused(&self) -> bool {
        lock(&self.shared).paused
    }

    pub fn active(&self) -> bool {
        lock(&self.shared).main.is_some()
    }

    pub fn crossfading(&self) -> bool {
        lock(&self.shared).out.is_some()
    }

    pub fn buffering(&self) -> bool {
        let st = lock(&self.shared);
        st.main.as_ref().is_some_and(|v| !v.ready) && !st.paused
    }

    pub fn error(&self) -> Option<String> {
        let st = lock(&self.shared);
        match &st.main {
            Some(v) => v.error.clone(),
            None => st.last_error.clone(),
        }
    }

    /// `true` una sola vez cuando la pista principal termina de sonar.
    pub fn poll_finished(&self) -> bool {
        let mut st = lock(&self.shared);
        std::mem::take(&mut st.finished)
    }

    pub fn viz_samples(&self, out: &mut Vec<f32>) {
        let st = lock(&self.shared);
        out.clear();
        out.extend(st.viz.iter().copied());
    }

    // ------------------------------------------------------------ decodificar
    fn restart(&self, id: u64, start: f64, fade: f64) {
        let rate = self.samplerate;
        let (token, source, filters) = {
            let mut st = lock(&self.shared);
            let filters = st.filters.clone();
            let Some(v) = st.voice_mut(id) else { return };
            let token = v.reset(start);
            v.set_fade(0.0, 1.0, (fade * rate as f64) as u64);
            let source = v.source.clone();
            st.finished = false;
            self.shared.1.notify_all();
            (token, source, filters)
        };
        let shared = self.shared.clone();
        let _ = thread::Builder::new()
            .name("decoder".into())
            .spawn(move || decode(shared, id, token, source, start, filters, rate));
    }
}

impl Drop for AudioEngine {
    fn drop(&mut self) {
        self.stop();
        self.stream = None;
    }
}

fn build_args(src: &Source, start: f64, filters: &str, rate: u32) -> Vec<String> {
    let mut a: Vec<String> = ["-nostdin", "-hide_banner", "-loglevel", "error"]
        .map(String::from)
        .into();
    if src.is_remote() {
        a.extend(
            [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_delay_max",
                "5",
            ]
            .map(String::from),
        );
        let mut headers = String::new();
        for (k, v) in &src.headers {
            if k.eq_ignore_ascii_case("user-agent") {
                a.extend(["-user_agent".into(), v.clone()]);
            } else {
                headers.push_str(&format!("{k}: {v}\r\n"));
            }
        }
        if !headers.is_empty() {
            a.extend(["-headers".into(), headers]);
        }
    }
    if start > 0.0 {
        a.extend(["-ss".into(), format!("{start:.3}")]);
    }
    a.extend([
        "-i".into(),
        src.input.clone(),
        "-vn".into(),
        "-sn".into(),
        "-dn".into(),
    ]);
    if !filters.is_empty() {
        a.extend(["-af".into(), filters.to_string()]);
    }
    a.extend(
        [
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            &CHANNELS.to_string(),
            "-ar",
            &rate.to_string(),
            "pipe:1",
        ]
        .map(String::from),
    );
    a
}

fn clean_error(lines: &[String]) -> String {
    // Quita prefijos "[in#0 @ 0x55d…] " de ffmpeg.
    let mut joined = String::new();
    let mut rest = lines.join(" ");
    while let Some(a) = rest.find('[') {
        match rest[a..].find(']') {
            Some(b) if rest[a..a + b].contains("@ 0x") => {
                joined.push_str(&rest[..a]);
                rest = rest[a + b + 1..].to_string();
            }
            _ => {
                joined.push_str(&rest[..=a]);
                rest = rest[a + 1..].to_string();
            }
        }
    }
    joined.push_str(&rest);
    for pat in [
        "Error opening input files:",
        "Error opening input files",
        "Error opening input file:",
        "Error opening input file",
    ] {
        joined = joined.replace(pat, " ");
    }
    // URLs (pueden llevar tokens) fuera.
    let msg = joined
        .split_whitespace()
        .filter(|w| !w.starts_with("http://") && !w.starts_with("https://"))
        .filter(|w| !(w.contains('&') && w.contains('=')))
        .collect::<Vec<_>>()
        .join(" ");
    let msg = msg
        .trim_matches(|c: char| c == ' ' || c == '.' || c == ':')
        .to_string();
    if msg.is_empty() {
        return "ffmpeg no devolvió audio".into();
    }
    let chars: Vec<char> = msg.chars().collect();
    chars[chars.len().saturating_sub(300)..].iter().collect()
}

fn decode(
    shared: Shared,
    id: u64,
    token: u64,
    src: Arc<Source>,
    start: f64,
    filters: String,
    rate: u32,
) {
    let current = |st: &mut State| st.voice_mut(id).is_some_and(|v| v.token == token);

    while unix_now() < src.available_at {
        if !current(&mut lock(&shared)) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }

    let fail = |msg: String| {
        let mut st = lock(&shared);
        if current(&mut st) {
            let v = st.voice_mut(id).expect("voz");
            v.error = Some(msg);
            v.decoder_done = true;
            v.ready = true;
        }
    };
    let Some(exe) = ffmpeg::ffmpeg_exe() else {
        fail(ffmpeg::NOT_FOUND.into());
        return;
    };
    let child = ffmpeg::command(exe)
        .args(build_args(&src, start, &filters, rate))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(e) => {
            fail(format!("No se pudo iniciar ffmpeg: {e}"));
            return;
        }
    };

    let err_lines = Arc::new(Mutex::new(VecDeque::<String>::new()));
    if let Some(stderr) = child.stderr.take() {
        let lines = err_lines.clone();
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(|l| l.ok()) {
                let mut q = lines.lock().unwrap_or_else(|e| e.into_inner());
                q.push_back(line.trim().to_string());
                if q.len() > 6 {
                    q.pop_front();
                }
            }
        });
    }

    let max_frames = (MAX_BUFFER_SECONDS * rate as f64) as usize;
    let prebuffer = (PREBUFFER_SECONDS * rate as f64) as usize;
    let mut total = 0usize;
    let mut stdout = child.stdout.take().expect("stdout");
    let mut buf = vec![0u8; READ_FRAMES * BYTES_PER_FRAME];
    let mut pending: Vec<u8> = Vec::with_capacity(buf.len() + BYTES_PER_FRAME);
    loop {
        let n = match stdout.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => n,
        };
        pending.extend_from_slice(&buf[..n]);
        let usable = pending.len() - pending.len() % BYTES_PER_FRAME;
        if usable == 0 {
            continue;
        }
        let samples: Vec<f32> = pending[..usable]
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .collect();
        pending.drain(..usable);
        let frames = samples.len() / CHANNELS;
        total += frames;

        let mut st = lock(&shared);
        loop {
            if !current(&mut st) {
                break;
            }
            if st.voice_mut(id).expect("voz").buffered < max_frames {
                break;
            }
            st = shared
                .1
                .wait_timeout(st, Duration::from_millis(250))
                .map(|r| r.0)
                .unwrap_or_else(|e| e.into_inner().0);
        }
        if !current(&mut st) {
            drop(st);
            let _ = child.kill();
            let _ = child.wait();
            return;
        }
        let v = st.voice_mut(id).expect("voz");
        v.chunks.push_back(samples);
        v.buffered += frames;
        if v.buffered >= prebuffer {
            v.ready = true;
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    // Damos un instante a que el hilo de stderr recoja las últimas líneas.
    thread::sleep(Duration::from_millis(20));
    let mut st = lock(&shared);
    if current(&mut st) {
        let v = st.voice_mut(id).expect("voz");
        v.decoder_done = true;
        v.ready = true;
        if total == 0 {
            let lines: Vec<String> = err_lines
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .iter()
                .cloned()
                .collect();
            v.error = Some(clean_error(&lines));
        }
    }
}

/// Callback de audio: mezcla, volumen con rampa, limitador suave y visualizador.
fn callback(shared: &Shared, mix: &mut [f32]) {
    let frames = mix.len() / CHANNELS;
    let mut st = lock(shared);
    if st.paused || (st.main.is_none() && st.out.is_none()) {
        return; // `mix` ya está a cero
    }
    let produced = st.render(mix);
    shared.1.notify_all();

    if produced > 0 {
        let take = produced.min(VIZ_SIZE);
        for _ in 0..take {
            st.viz.pop_front();
        }
        for f in produced - take..produced {
            st.viz.push_back((mix[f * 2] + mix[f * 2 + 1]) * 0.5);
        }
    }
    let (g0, mut g1) = (st.gain, st.target_gain);
    if g0 != g1 {
        let step = 0.08; // rampa de volumen por bloque
        g1 = if g1 > g0 {
            g1.min(g0 + step)
        } else {
            g1.max(g0 - step)
        };
        for f in 0..frames {
            let g = g0 + (g1 - g0) * (f as f32 / frames.max(2).saturating_sub(1) as f32);
            mix[f * 2] *= g;
            mix[f * 2 + 1] *= g;
        }
        st.gain = g1;
    } else {
        mix.iter_mut().for_each(|s| *s *= g1);
    }
    drop(st);
    // Limitador suave: el EQ puede pasarse de 0 dBFS; mejor redondear que recortar.
    let peak = mix.iter().fold(0.0f32, |m, s| m.max(s.abs()));
    if peak > 0.98 {
        mix.iter_mut().for_each(|s| *s = s.tanh());
    }
}

// ---------------------------------------------------------------- espectro
/// Bandas de espectro 0..1 en escala logarítmica (40 Hz–14 kHz), como en Python.
pub struct Spectrum {
    fft: Arc<dyn rustfft::Fft<f32>>,
    window: Vec<f32>,
    buf: Vec<rustfft::num_complex::Complex<f32>>,
    edges: Vec<usize>,
    key: (u32, usize),
}

impl Default for Spectrum {
    fn default() -> Self {
        Self::new()
    }
}

impl Spectrum {
    pub fn new() -> Spectrum {
        let fft = rustfft::FftPlanner::new().plan_fft_forward(VIZ_SIZE);
        let n = VIZ_SIZE as f32;
        let window = (0..VIZ_SIZE)
            .map(|i| 0.5 - 0.5 * (2.0 * std::f32::consts::PI * i as f32 / (n - 1.0)).cos())
            .collect();
        Spectrum {
            fft,
            window,
            buf: Vec::new(),
            edges: Vec::new(),
            key: (0, 0),
        }
    }

    pub fn bands(&mut self, samples: &[f32], samplerate: u32, bands: usize) -> Vec<f32> {
        if bands == 0 || samples.len() < VIZ_SIZE {
            return vec![0.0; bands];
        }
        self.buf.clear();
        self.buf.extend(
            samples[samples.len() - VIZ_SIZE..]
                .iter()
                .zip(&self.window)
                .map(|(s, w)| rustfft::num_complex::Complex::new(s * w, 0.0)),
        );
        self.fft.process(&mut self.buf);
        let half = VIZ_SIZE / 2 + 1;
        let spec: Vec<f32> = self.buf[..half]
            .iter()
            .map(|c| c.norm() / (VIZ_SIZE as f32 / 4.0))
            .collect();

        if self.key != (samplerate, bands) {
            let hi = (14000.0f64).min(samplerate as f64 / 2.0 - 1.0);
            let (lo_l, hi_l) = (40.0f64.ln(), hi.ln());
            let mut edges: Vec<usize> = (0..=bands)
                .map(|i| {
                    let f = (lo_l + (hi_l - lo_l) * i as f64 / bands as f64).exp();
                    ((f / samplerate as f64 * VIZ_SIZE as f64) as usize).clamp(1, half - 1)
                })
                .collect();
            for i in 1..edges.len() {
                edges[i] = edges[i].max(edges[i - 1] + 1);
            }
            edges.iter_mut().for_each(|e| *e = (*e).clamp(1, half));
            self.edges = edges;
            self.key = (samplerate, bands);
        }
        (0..bands)
            .map(|i| {
                let a = self.edges[i];
                let b = self.edges[i + 1].max(a + 1).min(half);
                let m = spec
                    .get(a..b)
                    .map_or(0.0, |s| s.iter().cloned().fold(0.0, f32::max));
                // Inclinación: los agudos tienen menos energía, los realzamos para que se vean.
                let tilt = if bands > 1 {
                    14.0 * i as f32 / (bands - 1) as f32
                } else {
                    0.0
                };
                let db = 20.0 * m.max(1e-6).log10() + tilt;
                ((db + 60.0) / 54.0).clamp(0.0, 1.0)
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn args_for_local_file() {
        let src = Source::file("/m/a.flac", Some(10.0));
        let a = build_args(&src, 12.5, "volume=-1.0dB", 44100);
        let s = a.join(" ");
        assert!(s.contains("-ss 12.500 -i /m/a.flac"));
        assert!(s.contains("-af volume=-1.0dB"));
        assert!(s.ends_with("-f f32le -acodec pcm_f32le -ac 2 -ar 44100 pipe:1"));
        assert!(!s.contains("-reconnect"));
    }

    #[test]
    fn fade_curve() {
        let mut v = Voice::new(1, Source::default());
        v.set_fade(0.0, 1.0, 100);
        assert_eq!(v.gain_at(0), 0.0);
        assert!((v.gain_at(50) - (std::f32::consts::FRAC_PI_4).sin()).abs() < 1e-6);
        assert_eq!(v.gain_at(100), 1.0);
    }

    #[test]
    fn mixing_consumes_buffer() {
        let mut v = Voice::new(1, Source::default());
        v.set_fade(1.0, 1.0, 1);
        v.fade_pos = 1;
        v.chunks.push_back(vec![0.5; 6]); // 3 frames
        v.chunks.push_back(vec![0.25; 4]); // 2 frames
        v.buffered = 5;
        let mut mix = vec![0.0; 8];
        assert_eq!(v.mix_into(&mut mix, true), 4);
        assert_eq!(mix, vec![0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.25, 0.25]);
        assert_eq!(v.buffered, 1);
        assert_eq!(v.played, 4);
    }

    #[test]
    fn spectrum_of_tone_peaks_in_band() {
        let mut sp = Spectrum::new();
        let rate = 48000;
        let samples: Vec<f32> = (0..VIZ_SIZE)
            .map(|i| (2.0 * std::f32::consts::PI * 1000.0 * i as f32 / rate as f32).sin())
            .collect();
        let b = sp.bands(&samples, rate, 16);
        let max_i = b
            .iter()
            .enumerate()
            .max_by(|x, y| x.1.total_cmp(y.1))
            .unwrap()
            .0;
        assert!((6..=10).contains(&max_i), "pico en {max_i}: {b:?}");
    }

    #[test]
    fn error_cleanup() {
        let e = clean_error(&[
            "[in#0 @ 0x55d] Error opening input file /x.mp3.".into(),
            "Error opening input files: No such file or directory".into(),
        ]);
        assert_eq!(e, "/x.mp3. No such file or directory");
    }

    #[test]
    fn decodes_file_with_ffmpeg() {
        if ffmpeg::ffmpeg_exe().is_none() {
            return;
        }
        let dir = std::env::temp_dir().join(format!("bs-audio-{}", crate::models::new_uid(6)));
        std::fs::create_dir_all(&dir).unwrap();
        let wav = dir.join("tono.wav");
        let ok = std::process::Command::new(ffmpeg::ffmpeg_exe().unwrap())
            .args([
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-y",
            ])
            .arg(&wav)
            .status()
            .unwrap()
            .success();
        assert!(ok);
        let shared: Shared = Arc::new((
            Mutex::new(State {
                main: Some(Voice::new(
                    1,
                    Source::file(&wav.to_string_lossy(), Some(1.0)),
                )),
                out: None,
                paused: false,
                finished: false,
                last_error: None,
                filters: String::new(),
                gain: 1.0,
                target_gain: 1.0,
                viz: VecDeque::from(vec![0.0; VIZ_SIZE]),
                next_id: 2,
            }),
            Condvar::new(),
        ));
        let token = lock(&shared).main.as_mut().unwrap().reset(0.0);
        let src = lock(&shared).main.as_ref().unwrap().source.clone();
        decode(
            shared.clone(),
            1,
            token,
            src,
            0.0,
            eq::filter_chain("Rock", true),
            48000,
        );
        let mut total = 0;
        let mut mix = vec![0.0f32; 1024 * CHANNELS];
        for _ in 0..200 {
            mix.iter_mut().for_each(|x| *x = 0.0);
            callback(&shared, &mut mix);
            let st = lock(&shared);
            if st.finished {
                break;
            }
            total += 1024;
        }
        let st = lock(&shared);
        assert!(st.finished, "no terminó");
        assert!(st.last_error.is_none(), "{:?}", st.last_error);
        assert!((47000..=50000).contains(&total), "frames: {total}");
        assert!(st.viz.iter().any(|x| x.abs() > 0.01));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn missing_file_reports_error() {
        let shared: Shared = Arc::new((
            Mutex::new(State {
                main: Some(Voice::new(1, Source::file("/no/existe.mp3", None))),
                out: None,
                paused: false,
                finished: false,
                last_error: None,
                filters: String::new(),
                gain: 1.0,
                target_gain: 1.0,
                viz: VecDeque::from(vec![0.0; VIZ_SIZE]),
                next_id: 2,
            }),
            Condvar::new(),
        ));
        let token = lock(&shared).main.as_mut().unwrap().reset(0.0);
        let src = lock(&shared).main.as_ref().unwrap().source.clone();
        decode(shared.clone(), 1, token, src, 0.0, String::new(), 48000);
        let mut mix = vec![0.0f32; 256];
        callback(&shared, &mut mix);
        let st = lock(&shared);
        assert!(st.finished);
        let e = st.last_error.clone().unwrap();
        assert!(e.contains("No such file"), "{e}");
    }
}
