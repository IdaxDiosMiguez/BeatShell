# ▚ BeatShell Desktop

La versión con ventana de BeatShell: el mismo motor (YouTube, Spotify, archivos locales,
descargas a MP3, ecualizador, modo DJ), con una interfaz limpia, rápida y fácil de usar.
Un ejecutable para **macOS, Windows y Linux**, sin instalar Python.

- Una barra arriba para todo: escribe y busca en YouTube, o pega un enlace de YouTube/Spotify.
- Doble clic para escuchar. Clic derecho para encolar, añadir a una playlist o descargar.
- Tu biblioteca a la izquierda, como en Spotify: cada playlist con su portada (o un mosaico con las
  de sus canciones), su nombre y «Playlist · autor»; al abrirla, portada grande y la de cada canción.
- **Importar de Spotify** (botón **+** de «Tu biblioteca», o pegando el enlace en la barra): una
  playlist, un álbum o un perfil entero (todas sus playlists públicas).
- **Temas**: los mismos que en la terminal (BeatShell, Game Boy, Sunset, Ice) más claro, oscuro y
  automático. Se cambian en vivo desde **Ajustes**.
- **Modo segundo plano** para jugar o trabajar con música: la ventana se cierra del todo y BeatShell
  se queda en la barra de menús / bandeja usando el mínimo de memoria y procesador.
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

## Segundo plano

Pulsa la luna 🌙 del reproductor (o `Ctrl/⌘ B`). La ventana no se oculta: se destruye. La música
sigue y BeatShell se queda como un icono en la barra de menús (macOS) o en la bandeja del sistema
(Windows, Linux), con un menú para pausar, saltar de canción, volver a abrirlo o salir.

| | Memoria total | CPU de la interfaz |
|---|---|---|
| Ventana abierta, sonando | ~525 MB | ~7 % |
| **Segundo plano** | **~250–320 MB** | **~0 %** |

*(Medido en un Mac con Apple Silicon reproduciendo desde YouTube; la cifra alta es la app empaquetada.)*

Cómo lo consigue:

- La ventana y todo lo que dibuja se destruyen, y la memoria libre se devuelve al sistema.
- YouTube se lee en un proceso aparte que se apaga en cuanto descansa (lo que más memoria deja
  tras de sí). La canción siguiente ya está preparada antes, así que la música no lo nota.
- El reproductor se despierta 4 veces por segundo en lugar de 20.

Por defecto, **cerrar la ventana mientras suena música** también pasa a segundo plano (se puede
cambiar en Ajustes). Para empezar directamente así, sin ventana:

```bash
beatshell-desktop --segundo-plano "lofi hip hop"
```

Si BeatShell ya está abierto, volver a abrirlo lo trae al frente (no arranca otro).
`--segundo-plano` lo manda a segundo plano y `--salir` lo cierra.

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
| `Ctrl/⌘ B` | Segundo plano |
| `Ctrl/⌘ ,` | Ajustes |
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
  shell.py     la app por encima de la ventana: temas, bandeja, segundo plano, instancia única
  app.py       ventana principal: barra lateral, buscador, páginas, reproductor
  settings_page.py  Ajustes
  player.py    cola, motor de audio, alta calidad en marcha, sin huecos, modo DJ, descargas
  extractor.py YouTube en un proceso aparte que se apaga solo
  widgets.py   listas de canciones (pintadas a mano), barras, portada, interruptores, avisos
  style.py     temas y hoja de estilo
  settings.py  ajustes propios del escritorio (desktop.json)
  memory.py    medir y devolver memoria
  icons.py     iconos vectoriales
  workers.py   hilos en segundo plano → resultados en la interfaz
packaging/     icono, especificación de PyInstaller, script de build y prueba de humo
```

Variables útiles: `BEATSHELL_THEME=dark|light|beatshell|gameboy|sunset|ice` fuerza un tema; `BEATSHELL_HOME=/ruta` usa otra
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
