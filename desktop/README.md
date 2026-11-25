# ▚ BeatShell Desktop

La versión con ventana de BeatShell: el mismo motor (YouTube, Spotify, archivos locales,
descargas a MP3, ecualizador, modo DJ), con una interfaz limpia, rápida y fácil de usar.
Un ejecutable para **macOS, Windows y Linux**, sin instalar Python.

- Una barra arriba para todo: escribe y busca en YouTube, o pega un enlace de YouTube/Spotify.
- Doble clic para escuchar. Clic derecho para encolar, añadir a una playlist o descargar.
- Tu biblioteca a la izquierda: tus carpetas de música, playlists, álbumes y artistas.
- Modo claro y oscuro automáticos, según el sistema.
- Comparte biblioteca, playlists, cola y ajustes con la versión de terminal.

## Descargar

En la página de *Releases* del repositorio:

| Sistema | Archivo | Cómo abrirlo |
|---|---|---|
| macOS | `BeatShell-…-macos-arm64.dmg` (Apple Silicon) / `-x64.dmg` (Intel) | Arrastra BeatShell a Aplicaciones. La primera vez: clic derecho → **Abrir**. |
| Windows | `BeatShell-…-windows-x64.zip` | Descomprime y abre `BeatShell.exe`. Si sale SmartScreen: **Más información → Ejecutar de todas formas**. |
| Linux | `BeatShell-…-linux-x64.tar.gz` | `tar xzf …` y ejecuta `BeatShell/BeatShell`. Necesita `libportaudio2`. |

ffmpeg y Deno ya van dentro: no hace falta instalar nada más.

> La app todavía no está firmada, por eso macOS y Windows avisan la primera vez.

## Atajos

| Tecla | Acción |
|---|---|
| `Espacio` | Reproducir / pausa |
| `Ctrl/⌘ →` `Ctrl/⌘ ←` | Siguiente / anterior |
| `Mayús →` `Mayús ←` | ±10 s |
| `Ctrl/⌘ ↑` `Ctrl/⌘ ↓` · `M` | Volumen · silencio |
| `Ctrl/⌘ F` o `/` | Ir al buscador |
| `Ctrl/⌘ S` · `Ctrl/⌘ R` | Aleatorio · repetir |
| `Ctrl/⌘ 1…4` | Buscar · Cola · Biblioteca · Descargas |
| `Ctrl/⌘ O` · `Ctrl/⌘ N` | Añadir carpeta de música · nueva playlist |
| `Ctrl/⌘ D` | Modo DJ |
| `Supr` | Quitar de la cola / de la playlist |

Las teclas multimedia del teclado (▶︎⏸ ⏭ ⏮) también funcionan mientras la ventana está activa.
También puedes **arrastrar** carpetas, archivos de audio, `.m3u8` o enlaces a la ventana.

## Desarrollo

La app de escritorio reutiliza el núcleo de BeatShell (`../src/BeatShell`) sin tocarlo; aquí
solo vive la interfaz (Qt / PySide6).

```bash
cd desktop
python3 -m venv .venv
.venv/bin/pip install -e .. -e ".[build]"
.venv/bin/beatshell-desktop            # o: .venv/bin/python -m beatshell_desktop
```

```
beatshell_desktop/
  app.py       ventana principal: barra lateral, buscador, páginas, reproductor
  player.py    cola, motor de audio, alta calidad en marcha, sin huecos, modo DJ, descargas
  widgets.py   listas de canciones (pintadas a mano: miles de filas sin tirones), portada, avisos
  style.py     colores (claro/oscuro) y hoja de estilo
  icons.py     iconos vectoriales
  workers.py   hilos en segundo plano → resultados en la interfaz
packaging/     icono, especificación de PyInstaller, script de build y prueba de humo
```

Variables útiles: `BEATSHELL_THEME=light|dark` fuerza un tema; `BEATSHELL_HOME=/ruta` usa otra
carpeta de datos (pruebas).

### Construir el ejecutable

```bash
.venv/bin/python packaging/build.py --deno
```

Deja la app y el archivo para compartir (`.dmg`, `.zip` o `.tar.gz`) en `desktop/dist/`.
Cada sistema se construye en su propio sistema; GitHub Actions
(`.github/workflows/desktop.yml`) construye los cuatro (macOS ARM e Intel, Windows y Linux)
en cada PR, y al subir una etiqueta `desktop-v*` crea un borrador de release con todos.

¿Por qué es rápida?

- La ventana aparece antes de cargar nada pesado (yt-dlp y el motor de audio arrancan detrás).
- El audio corre en su propio proceso: la interfaz nunca provoca cortes.
- YouTube suena al instante con un stream rápido y cambia a alta calidad sin cortes.
- El ejecutable va en modo carpeta (no un único archivo que haya que descomprimir al abrir).
