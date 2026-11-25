//! Dibujo de la interfaz con ratatui. Colores del tema "beatshell" de la versión Python.

use ratatui::Frame;
use ratatui::layout::{Alignment, Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Borders, Cell, List, ListItem, ListState, Paragraph, Row, Table, TableState,
};

use crate::app::{App, Focus, View};
use crate::models::{Track, fmt_time};
use crate::playqueue::Repeat;

const PRIMARY: Color = Color::Rgb(0xff, 0x4f, 0xd8);
const SECONDARY: Color = Color::Rgb(0x5e, 0xe7, 0xff);
const ACCENT: Color = Color::Rgb(0xff, 0xd1, 0x66);
const FG: Color = Color::Rgb(0xec, 0xe6, 0xff);
const MUTED: Color = Color::Rgb(0x8a, 0x80, 0xb0);
const BG: Color = Color::Rgb(0x0f, 0x0b, 0x1e);
const PANEL: Color = Color::Rgb(0x22, 0x1a, 0x44);

fn block(title: &str, focused: bool) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(if focused { PRIMARY } else { PANEL }))
        .title(Span::styled(
            format!(" {title} "),
            Style::new()
                .fg(if focused { PRIMARY } else { MUTED })
                .bold(),
        ))
}

pub fn viz_bands_for(width: u16) -> usize {
    (width.saturating_sub(2) as usize / 2).clamp(8, 48)
}

pub fn draw(f: &mut Frame, app: &App) {
    let area = f.area();
    f.render_widget(Block::default().style(Style::new().bg(BG).fg(FG)), area);
    let [header, input, body, player, footer] = Layout::vertical([
        Constraint::Length(1),
        Constraint::Length(3),
        Constraint::Min(5),
        Constraint::Length(7),
        Constraint::Length(1),
    ])
    .areas(area);

    draw_header(f, app, header);
    draw_input(f, app, input);
    match app.view {
        View::Queue => draw_queue(f, app, body),
        View::Library => draw_library(f, app, body),
    }
    draw_player(f, app, player);
    draw_footer(f, app, footer);
}

fn draw_header(f: &mut Frame, app: &App, area: Rect) {
    let tab = |n: &str, label: &str, on: bool| {
        let st = if on {
            Style::new().fg(BG).bg(PRIMARY).bold()
        } else {
            Style::new().fg(MUTED)
        };
        Span::styled(format!(" {n} {label} "), st)
    };
    let line = Line::from(vec![
        Span::styled("▚ ", Style::new().fg(SECONDARY)),
        Span::styled("BeatShell", Style::new().fg(PRIMARY).bold()),
        Span::styled("  rust  ", Style::new().fg(MUTED).italic()),
        tab("1", "Cola", app.view == View::Queue),
        Span::raw(" "),
        tab("3", "Biblioteca", app.view == View::Library),
    ]);
    f.render_widget(Paragraph::new(line), area);
    if let Some(msg) = app
        .status()
        .map(str::to_string)
        .or_else(|| app.engine_error.clone())
    {
        let w = area.width.saturating_sub(40) as usize;
        let msg: String = msg.chars().take(w).collect();
        f.render_widget(
            Paragraph::new(Span::styled(msg, Style::new().fg(ACCENT))).alignment(Alignment::Right),
            area,
        );
    }
}

fn draw_input(f: &mut Frame, app: &App, area: Rect) {
    let (text, style, focused) = match &app.input {
        Some(s) => (format!("{s}▏"), Style::new().fg(FG), true),
        None => (
            "Pulsa / y escribe la ruta de una carpeta, un archivo de audio o un .m3u".to_string(),
            Style::new().fg(MUTED),
            false,
        ),
    };
    let p = Paragraph::new(Line::from(vec![
        Span::styled("› ", Style::new().fg(PRIMARY)),
        Span::styled(text, style),
    ]))
    .block(block("Buscar", focused));
    f.render_widget(p, area);
}

fn track_row<'a>(i: usize, t: &'a Track, current: bool) -> Row<'a> {
    let marker = if current { "▶" } else { t.icon() };
    let st = if current {
        Style::new().fg(PRIMARY).bold()
    } else {
        Style::new().fg(FG)
    };
    Row::new(vec![
        Cell::from(format!("{:>3}", i + 1)).style(Style::new().fg(MUTED)),
        Cell::from(marker).style(Style::new().fg(SECONDARY)),
        Cell::from(t.title.as_str()),
        Cell::from(t.display_artist()).style(Style::new().fg(MUTED)),
        Cell::from(t.album.as_str()).style(Style::new().fg(MUTED)),
        Cell::from(fmt_time(t.duration)).style(Style::new().fg(MUTED)),
    ])
    .style(st)
}

fn tracks_table<'a>(rows: Vec<Row<'a>>, title: String, focused: bool) -> Table<'a> {
    let widths = [
        Constraint::Length(4),
        Constraint::Length(2),
        Constraint::Percentage(42),
        Constraint::Percentage(26),
        Constraint::Percentage(22),
        Constraint::Length(7),
    ];
    Table::new(rows, widths)
        .header(
            Row::new(["  #", "", "Título", "Artista", "Álbum", "Dur."])
                .style(Style::new().fg(SECONDARY).add_modifier(Modifier::BOLD)),
        )
        .block(block(&title, focused))
        .row_highlight_style(Style::new().bg(PANEL))
        .highlight_symbol(if focused { "▌" } else { " " })
}

fn draw_queue(f: &mut Frame, app: &App, area: Rect) {
    let q = &app.queue;
    if q.is_empty() {
        let p = Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled(
                "La cola está vacía",
                Style::new().fg(FG).bold(),
            )),
            Line::from(Span::styled(
                "Ve a la biblioteca (3) o escribe una ruta con /",
                Style::new().fg(MUTED),
            )),
        ])
        .alignment(Alignment::Center)
        .block(block("Cola", true));
        f.render_widget(p, area);
        return;
    }
    let rows: Vec<Row> = q
        .tracks
        .iter()
        .enumerate()
        .map(|(i, t)| track_row(i, t, i as isize == q.index))
        .collect();
    let total: f64 = q.tracks.iter().filter_map(|t| t.duration).sum();
    let title = format!("Cola · {} canciones · {}", q.len(), fmt_time(Some(total)));
    let mut state = TableState::default().with_selected(Some(app.queue_sel));
    f.render_stateful_widget(tracks_table(rows, title, true), area, &mut state);
}

fn draw_library(f: &mut Frame, app: &App, area: Rect) {
    let [left, right] =
        Layout::horizontal([Constraint::Percentage(30), Constraint::Percentage(70)]).areas(area);
    let sources = app.sources();
    let items: Vec<ListItem> = sources
        .iter()
        .map(|(_, label)| ListItem::new(label.clone()))
        .collect();
    let title = if app.scanning {
        "Biblioteca · escaneando…"
    } else {
        "Biblioteca"
    };
    let list = List::new(items)
        .block(block(title, app.focus == Focus::Sources))
        .highlight_style(Style::new().bg(PANEL).fg(PRIMARY).bold())
        .highlight_symbol("▌");
    let mut st = ListState::default().with_selected(Some(app.source_sel));
    f.render_stateful_widget(list, left, &mut st);

    let current_key = app.queue.current().map(Track::key);
    let rows: Vec<Row> = app
        .lib_tracks
        .iter()
        .enumerate()
        .map(|(i, t)| {
            track_row(
                i,
                t,
                app.playing() && current_key.as_deref() == Some(t.key().as_str()),
            )
        })
        .collect();
    let title = format!("{} canciones", app.lib_tracks.len());
    let mut st = TableState::default().with_selected(Some(app.track_sel));
    f.render_stateful_widget(
        tracks_table(rows, title, app.focus == Focus::Tracks),
        right,
        &mut st,
    );
}

fn draw_player(f: &mut Frame, app: &App, area: Rect) {
    let outer = block("Sonando", false);
    let inner = outer.inner(area);
    f.render_widget(outer, area);
    let [info, viz] =
        Layout::horizontal([Constraint::Percentage(55), Constraint::Percentage(45)]).areas(inner);

    let cur = app.queue.current();
    let (title, sub) = match cur {
        Some(t) => (
            t.title.clone(),
            if t.album.is_empty() {
                t.display_artist().to_string()
            } else {
                format!("{} · {}", t.display_artist(), t.album)
            },
        ),
        None => ("Nada sonando".to_string(), "—".to_string()),
    };
    let state = if !app.playing() {
        "■"
    } else if app.paused() {
        "⏸"
    } else if app.buffering() {
        "…"
    } else {
        "▶"
    };
    let pos = if app.playing() { app.position() } else { 0.0 };
    let dur = app.duration();
    let bar_w = info.width.saturating_sub(16) as usize;
    let ratio = dur
        .filter(|d| *d > 0.0)
        .map_or(0.0, |d| (pos / d).clamp(0.0, 1.0));
    let filled = (ratio * bar_w as f64).round() as usize;
    let progress = Line::from(vec![
        Span::styled(format!("{:>7} ", fmt_time(Some(pos))), Style::new().fg(FG)),
        Span::styled("━".repeat(filled), Style::new().fg(PRIMARY)),
        Span::styled("●", Style::new().fg(ACCENT)),
        Span::styled(
            "─".repeat(bar_w.saturating_sub(filled)),
            Style::new().fg(PANEL),
        ),
        Span::styled(format!(" {}", fmt_time(dur)), Style::new().fg(MUTED)),
    ]);
    let on = |b: bool| {
        if b {
            Style::new().fg(SECONDARY).bold()
        } else {
            Style::new().fg(MUTED)
        }
    };
    let repeat = match app.queue.repeat {
        Repeat::Off => "↻ no",
        Repeat::All => "↻ todo",
        Repeat::One => "↻ una",
    };
    let vol = app.volume();
    let vol_bar = format!(
        "{}{}",
        "▮".repeat((vol / 10) as usize),
        "▯".repeat(10 - (vol / 10) as usize)
    );
    let toggles = Line::from(vec![
        Span::styled(format!("vol {vol_bar} {vol:>3}%  "), Style::new().fg(FG)),
        Span::styled("⤨ aleatorio  ", on(app.queue.shuffle())),
        Span::styled(format!("{repeat}  "), on(app.queue.repeat != Repeat::Off)),
        Span::styled(
            format!("EQ {}  ", app.cfg.eq_preset),
            on(app.cfg.eq_preset != "Plano"),
        ),
        Span::styled("DJ", on(app.cfg.dj_mode)),
    ]);
    let lines = vec![
        Line::from(vec![
            Span::styled(format!("{state} "), Style::new().fg(SECONDARY)),
            Span::styled(title, Style::new().fg(FG).bold()),
        ]),
        Line::from(Span::styled(format!("  {sub}"), Style::new().fg(MUTED))),
        Line::from(""),
        progress,
        toggles,
    ];
    f.render_widget(Paragraph::new(lines), info);
    draw_viz(f, app, viz);
}

/// Espectro con bloques ▁▂▃▄▅▆▇█ y degradado de colores, de abajo a arriba.
fn draw_viz(f: &mut Frame, app: &App, area: Rect) {
    const LEVELS: [char; 9] = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
    let rows = area.height as usize;
    if rows == 0 || app.bands.is_empty() {
        return;
    }
    let colors = [SECONDARY, Color::Rgb(0xa0, 0x9c, 0xff), PRIMARY, ACCENT];
    let mut lines = Vec::with_capacity(rows);
    for r in (0..rows).rev() {
        let color = colors[(r * colors.len() / rows).min(colors.len() - 1)];
        let mut s = String::new();
        for &b in &app.bands {
            let h = b * rows as f32 * 8.0;
            let cell = (h - r as f32 * 8.0).clamp(0.0, 8.0) as usize;
            s.push(LEVELS[cell]);
            s.push(' ');
        }
        lines.push(Line::from(Span::styled(s, Style::new().fg(color))));
    }
    f.render_widget(Paragraph::new(lines).alignment(Alignment::Right), area);
}

fn draw_footer(f: &mut Frame, app: &App, area: Rect) {
    let keys: &[(&str, &str)] = if app.input.is_some() {
        &[("Enter", "aceptar"), ("Esc", "cancelar")]
    } else {
        match app.view {
            View::Queue => &[
                ("␣", "play/pausa"),
                ("n/b", "sig/ant"),
                ("←→", "±5 s"),
                ("+/−", "volumen"),
                ("Supr", "quitar"),
                ("[ ]", "mover"),
                ("s", "aleatorio"),
                ("r", "repetir"),
                ("E", "EQ"),
                ("j", "DJ"),
                ("q", "salir"),
            ],
            View::Library => &[
                ("Enter", "reproducir"),
                ("a", "encolar"),
                ("Tab", "panel"),
                ("R", "re-escanear"),
                ("␣", "play/pausa"),
                ("n/b", "sig/ant"),
                ("E", "EQ"),
                ("q", "salir"),
            ],
        }
    };
    let mut spans = Vec::new();
    for (k, d) in keys {
        spans.push(Span::styled(
            format!(" {k} "),
            Style::new().fg(BG).bg(SECONDARY),
        ));
        spans.push(Span::styled(format!(" {d}  "), Style::new().fg(MUTED)));
    }
    f.render_widget(Paragraph::new(Line::from(spans)), area);
}
