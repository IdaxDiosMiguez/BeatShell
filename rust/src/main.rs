//! BeatShell en Rust: reproductor de música para la terminal (ratatui + cpal + ffmpeg).

// Parte de la API ya portada (playlists, búsqueda…) la usará la fase 2.
#![allow(dead_code)]

mod app;
mod audio;
mod config;
mod eq;
mod ffmpeg;
mod library;
mod models;
mod playqueue;
mod sources;
mod ui;

use std::time::{Duration, Instant};

use ratatui::crossterm::event::{
    self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers,
};
use ratatui::crossterm::execute;

use app::{App, Focus, View};

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--version" || a == "-V") {
        println!("BeatShell {VERSION} (Rust)");
        return Ok(());
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!(
            "Uso: beatshell [RUTA]\n\n\
             Reproductor de música para terminal.\n\
             RUTA puede ser una carpeta (se añade a la biblioteca), un archivo de audio o un .m3u/.m3u8.\n\
             En la barra (/) también se puede buscar en YouTube o pegar enlaces de YouTube y Spotify.\n\n\
             Variables: BEATSHELL_HOME (carpeta de datos), BEATSHELL_FFMPEG (ruta a ffmpeg),\n\
             BEATSHELL_YTDLP (ruta a yt-dlp)."
        );
        return Ok(());
    }
    let initial = args.join(" ");

    let mut app = App::new();
    if ffmpeg::ffmpeg_exe().is_none() {
        app.notify(ffmpeg::NOT_FOUND);
    }
    if !initial.trim().is_empty() {
        app.submit(&initial);
    }

    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableBracketedPaste);
    let result = run(&mut terminal, &mut app);
    let _ = execute!(std::io::stdout(), DisableBracketedPaste);
    ratatui::restore();
    app.save_session();
    result
}

fn run(terminal: &mut ratatui::DefaultTerminal, app: &mut App) -> anyhow::Result<()> {
    let frame = Duration::from_millis(33);
    let mut last = Instant::now();
    while !app.quit {
        let width = terminal.size()?.width;
        app.tick(ui::viz_bands_for(width * 45 / 100));
        terminal.draw(|f| ui::draw(f, app))?;
        let timeout = frame.saturating_sub(last.elapsed());
        if event::poll(timeout)? {
            match event::read()? {
                Event::Key(k) if k.kind != KeyEventKind::Release => handle_key(app, k),
                Event::Paste(text) => {
                    let s = app.input.get_or_insert_with(String::new);
                    s.push_str(text.trim_end_matches(['\r', '\n']));
                }
                _ => {}
            }
        }
        if last.elapsed() >= frame {
            last = Instant::now();
        }
    }
    Ok(())
}

fn handle_key(app: &mut App, k: KeyEvent) {
    if k.modifiers.contains(KeyModifiers::CONTROL) && k.code == KeyCode::Char('c') {
        app.quit = true;
        return;
    }
    if let Some(input) = app.input.as_mut() {
        match k.code {
            KeyCode::Esc => app.input = None,
            KeyCode::Enter => {
                let text = app.input.take().unwrap_or_default();
                app.submit(&text);
            }
            KeyCode::Backspace => {
                input.pop();
            }
            KeyCode::Char(c) => input.push(c),
            _ => {}
        }
        return;
    }

    match k.code {
        KeyCode::Char('q') => app.quit = true,
        KeyCode::Char('/') => app.input = Some(String::new()),
        KeyCode::Char('f') => app.input = Some(String::new()),
        KeyCode::Char(' ') => app.toggle_pause(),
        KeyCode::Char('n') => app.next(),
        KeyCode::Char('b') => app.prev(),
        KeyCode::Char('+') | KeyCode::Char('=') => app.change_volume(5),
        KeyCode::Char('-') => app.change_volume(-5),
        KeyCode::Char('m') => app.toggle_mute(),
        KeyCode::Char('s') => app.toggle_shuffle(),
        KeyCode::Char('r') => app.cycle_repeat(),
        KeyCode::Char('E') => app.cycle_eq(),
        KeyCode::Char('j') => app.toggle_dj(),
        KeyCode::Char('R') => app.rescan(),
        KeyCode::Char('1') => app.view = View::Queue,
        KeyCode::Char('2') => app.view = View::Search,
        KeyCode::Char('3') => {
            app.view = View::Library;
            app.refresh_lib_tracks();
        }
        KeyCode::Left => app.seek(-5.0),
        KeyCode::Right => app.seek(5.0),
        _ => match app.view {
            View::Queue => queue_key(app, k),
            View::Library => library_key(app, k),
            View::Search => search_key(app, k),
        },
    }
}

fn step(sel: usize, len: usize, delta: isize) -> usize {
    if len == 0 {
        return 0;
    }
    (sel as isize + delta).clamp(0, len as isize - 1) as usize
}

fn nav_delta(code: KeyCode) -> Option<isize> {
    match code {
        KeyCode::Up => Some(-1),
        KeyCode::Down => Some(1),
        KeyCode::PageUp => Some(-10),
        KeyCode::PageDown => Some(10),
        KeyCode::Home => Some(-1_000_000),
        KeyCode::End => Some(1_000_000),
        _ => None,
    }
}

fn queue_key(app: &mut App, k: KeyEvent) {
    if let Some(d) = nav_delta(k.code) {
        app.queue_sel = step(app.queue_sel, app.queue.len(), d);
        return;
    }
    match k.code {
        KeyCode::Enter => app.activate_queue(),
        KeyCode::Delete | KeyCode::Char('x') => app.remove_from_queue(),
        KeyCode::Char('[') => app.move_in_queue(-1),
        KeyCode::Char(']') => app.move_in_queue(1),
        KeyCode::Char('c') => {
            app.queue.clear(true);
            app.queue_sel = 0;
        }
        _ => {}
    }
}

fn search_key(app: &mut App, k: KeyEvent) {
    if let Some(d) = nav_delta(k.code) {
        app.online.result_sel = step(app.online.result_sel, app.result_tracks().len(), d);
        return;
    }
    match k.code {
        KeyCode::Enter => app.activate_result(),
        KeyCode::Char('a') => app.enqueue_results(false),
        KeyCode::Char('A') => app.enqueue_results(true),
        KeyCode::Char('i') => app.play_result_next(),
        KeyCode::Esc => app.view = View::Queue,
        _ => {}
    }
}

fn library_key(app: &mut App, k: KeyEvent) {
    if let Some(d) = nav_delta(k.code) {
        match app.focus {
            Focus::Sources => {
                let n = app.sources().len();
                let new = step(app.source_sel, n, d);
                if new != app.source_sel {
                    app.source_sel = new;
                    app.track_sel = 0;
                    app.refresh_lib_tracks();
                }
            }
            Focus::Tracks => app.track_sel = step(app.track_sel, app.lib_tracks.len(), d),
        }
        return;
    }
    match k.code {
        KeyCode::Tab | KeyCode::BackTab => {
            app.focus = if app.focus == Focus::Sources {
                Focus::Tracks
            } else {
                Focus::Sources
            }
        }
        KeyCode::Esc => app.focus = Focus::Sources,
        KeyCode::Enter => app.activate_library(),
        KeyCode::Char('a') => app.enqueue_selection(),
        _ => {}
    }
}
