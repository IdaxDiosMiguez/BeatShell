# BeatShell en Rust (fases 1 y 2)

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
Para YouTube y Spotify hace falta además [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) en el
`PATH` (o su ruta en `BEATSHELL_YTDLP`), y conviene tener `deno`, `node` o `bun` para que yt-dlp
pueda descifrar las firmas de YouTube.
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
| `app.rs`, `ui.rs` | Interfaz mínima: cola, búsqueda, biblioteca, barra de entrada, reproductor y visualizador. |
| `sources/mod.rs` | `classify()`: distingue búsqueda, enlaces de Spotify, vídeos y playlists de YouTube. |
| `sources/youtube.rs` | yt-dlp como subproceso (`yt-dlp -J`): búsqueda, playlists, streams (arranque rápido + alta calidad, con caché hasta que caducan), emparejado de canciones de Spotify y descarga a MP3. |
| `sources/spotify.rs` | Playlists, álbumes y canciones de Spotify: página de embed (sin cuenta, máx. 100 canciones) o la Web API si hay `spotify_client_id` y `spotify_client_secret` en `config.json`. |
| `app/online.rs` | Búsquedas y resolución de streams en hilos; las canciones de Spotify se emparejan con YouTube al reproducirlas. |

Compatibilidad comprobada: Python lee los JSON que escribe Rust (y al revés), y al re-escanear
reutiliza el índice de Rust sin releer los archivos.

## Teclas

`/` buscar en YouTube, pegar un enlace de YouTube/Spotify o una ruta (con `+` delante suena el primer resultado) · `␣` play/pausa · `n`/`b` siguiente/anterior · `←`/`→` ±5 s ·
`+`/`−` volumen · `m` silencio · `s` aleatorio · `r` repetir · `E` siguiente preset de EQ ·
`j` modo DJ · `1` cola · `2` resultados de búsqueda · `3` biblioteca · `Tab` cambiar de panel · `Enter` reproducir ·
`a` encolar (`A` todos los resultados, `i` a continuación) · `Supr`/`x` quitar de la cola · `[`/`]` mover · `c` vaciar la cola · `R` re-escanear · `q` salir.

## Pendiente (siguientes fases)

El cambio en marcha del stream rápido al de alta calidad, fundidos DJ con pistas online, la
interfaz de descargas a MP3 (la función ya está en `sources/youtube.rs`), gestión de playlists y
carpetas de la colección, pantalla del ecualizador, portadas (pixel art / Kitty), temas y ayuda.
