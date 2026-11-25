# BeatShell en Rust (fase 1)

Reescritura de BeatShell con [ratatui](https://ratatui.rs) + crossterm, audio con
[cpal](https://github.com/RustAudio/cpal) y decodificación con ffmpeg como subproceso.
La versión Python sigue intacta en `src/`; las dos comparten la misma carpeta de datos.

```bash
cd rust
cargo run --release -- ~/Música     # añade la carpeta y la escanea
cargo run --release -- disco.flac    # reproduce un archivo
cargo test
```

Requisitos: Rust 1.89+ y `ffmpeg` en el `PATH` (o su ruta en `BEATSHELL_FFMPEG`).
En Linux, cpal necesita las cabeceras de ALSA: `sudo apt install libasound2-dev`
(Debian/Ubuntu), `alsa-lib-devel` (Fedora) o `alsa-lib` (Arch).

## Qué hay en esta fase

| Módulo | Qué hace |
|---|---|
| `models.rs` | `Track`, igual que `Track.to_dict()` de Python (mismas claves y `key()` idéntica). |
| `config.rs` | Rutas de datos (las de platformdirs, o `BEATSHELL_HOME`) y `config.json`. Conserva las claves que no conoce. |
| `playqueue.rs` | La cola: aleatorio que se puede deshacer, repetir (no/todo/una), mover, quitar. |
| `library.rs` | `library.json` (carpetas + índice), `playlists.json`, `session.json` y lectura de `.m3u`. |
| `eq.rs` | Los 13 presets del ecualizador y el nivelado del modo DJ (misma cadena `-af`). |
| `audio.rs` | Motor: ffmpeg → PCM f32 → cpal, dos voces para fundidos, volumen con rampa, limitador suave y espectro (FFT). |
| `app.rs`, `ui.rs` | Interfaz mínima: cola, biblioteca, barra de entrada, reproductor y visualizador. |

Compatibilidad comprobada: Python lee los JSON que escribe Rust (y al revés), y al re-escanear
reutiliza el índice de Rust sin releer los archivos.

## Teclas

`/` escribir una ruta · `␣` play/pausa · `n`/`b` siguiente/anterior · `←`/`→` ±5 s ·
`+`/`−` volumen · `m` silencio · `s` aleatorio · `r` repetir · `E` siguiente preset de EQ ·
`j` modo DJ · `1` cola · `3` biblioteca · `Tab` cambiar de panel · `Enter` reproducir ·
`a` encolar · `Supr`/`x` quitar de la cola · `[`/`]` mover · `c` vaciar la cola · `R` re-escanear · `q` salir.

## Pendiente (siguientes fases)

YouTube (yt-dlp como subproceso) y Spotify, búsqueda, descargas a MP3, gestión de playlists y
carpetas de la colección, pantalla del ecualizador, portadas (pixel art / Kitty), temas y ayuda.
