# BeatShell para Android

La versión para móvil de BeatShell: busca en YouTube y reproduce, abre enlaces de YouTube y
Spotify, cola con aleatorio y repetir, música del teléfono, playlists guardadas y descargas.
Sigue sonando con la pantalla apagada y con la app cerrada, con notificación multimedia,
controles en la pantalla de bloqueo y respuesta a los botones de auriculares y Bluetooth.

Misma estética que la versión de escritorio: fondo oscuro neutro, un único acento rosa y modo
claro/oscuro automático según el sistema.

## Instalar el APK

1. Descarga `BeatShell.apk`:
   - de la página de [Releases](https://github.com/IdaxDiosMiguez/BeatShell/releases) (versiones
     publicadas con una etiqueta `android-v…`), o
   - de la pestaña **Actions** → *BeatShell Android* → la última ejecución en verde → artefacto
     **BeatShell-Android** (es un `.zip` que contiene el `.apk`).
2. Ábrelo en el teléfono. Android pedirá permiso para **instalar apps de origen desconocido**
   desde el navegador o el gestor de archivos que uses: actívalo para esa app.
3. Al abrir BeatShell por primera vez, permite las **notificaciones** (sin ellas no se ve el
   reproductor en la notificación ni en la pantalla de bloqueo). El permiso de **música y audio**
   solo se pide al entrar en *Biblioteca → Música del teléfono*.

Requisitos: Android 7.0 o superior en un teléfono de 64 bits (prácticamente todos desde 2017).

> **Si la música se corta con la pantalla apagada** en algunos Xiaomi, Huawei, OnePlus o Samsung:
> en *Ajustes → Batería*, quita las restricciones de BeatShell («Sin restricciones»). Esas capas
> cierran apps en segundo plano aunque estén reproduciendo. Más en <https://dontkillmyapp.com>.

> **Actualizar sobre una versión anterior**: Android solo instala una versión nueva encima si está
> firmada con la misma clave. Los APK de Actions se firman con la clave de depuración de cada
> ejecución salvo que el repositorio tenga configurado el keystore (ver más abajo); si Android dice
> «La app no se ha instalado», desinstala la versión anterior primero (las playlists se pierden:
> expórtalas antes desde *Biblioteca → ⋮ → Exportar playlists.json*).

## Uso

- **Buscar**: escribe y pulsa buscar. Tocar un resultado lo pone a sonar sin tocar el resto de la cola.
- **Enlaces**: pega un enlace en la barra (o *Pegar enlace*), o compártelo desde YouTube, Spotify o el
  navegador con **Compartir → BeatShell**. Admite vídeos y playlists de YouTube y canciones, álbumes
  y playlists de Spotify (cada canción de Spotify se busca en YouTube al reproducirla).
- **Importar de Spotify**: *Biblioteca → Importar de Spotify* (o comparte el enlace con BeatShell).
  Con el enlace de una playlist o un álbum se guarda en la biblioteca; con el de un **perfil**, todas sus
  playlists públicas (las 10 primeras: Spotify no enseña más sin cuenta). Se ven como en Spotify: portada,
  nombre y autor, y la portada del álbum en cada canción. Importar otra vez actualiza en vez de duplicar.
  Desde Android 12, los enlaces que tocas en el navegador o en otras apps se abren en su app
  oficial o en el navegador; para que BeatShell aparezca en «Abrir con», actívalo en *Ajustes del
  sistema → Apps → BeatShell → Abrir de forma predeterminada → Añadir enlace*. Compartir funciona siempre.
- **Cola**: pestaña *Cola*. Tocar salta a esa canción; el menú `⋮` permite quitar o mover.
  Aleatorio y repetir (todo / una) están en el reproductor a pantalla completa.
- **Menú de cada canción** (`⋮` o pulsación larga): reproducir a continuación, añadir a la cola,
  añadir a una playlist, descargar.
- **Biblioteca**: música del teléfono, playlists guardadas (también álbumes y playlists de
  YouTube/Spotify con *⋮ → Guardar en biblioteca*), descargas y ajustes. Las playlists usan el
  mismo `playlists.json` que la versión de escritorio: se pueden importar y exportar.
- **Descargas**: se guardan en `Música/BeatShell` en **M4A** (el audio AAC original de YouTube,
  sin recodificar) con título, artista y portada. Aparecen también en *Música del teléfono*.

## Construir

Necesitas:

- **JDK 17 o superior** (probado con Temurin 21 y 25).
- **Android SDK** con la plataforma 36 (Android Studio la instala; o `sdkmanager "platforms;android-36"`).
  Indica dónde está con `ANDROID_HOME` o con `sdk.dir=…` en `android/local.properties`.
- **Python 3.13** en el `PATH` como `python3.13` (Chaquopy lo usa para instalar yt-dlp en el APK).

```bash
cd android
./gradlew assembleDebug      # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # app/build/outputs/apk/release/app-release.apk
```

El APK de *release* sale minimizado con R8 y firmado. Para firmarlo con una clave propia (y que las
actualizaciones se instalen encima), crea un keystore una vez:

```bash
keytool -genkeypair -v -keystore beatshell.jks -alias beatshell -keyalg RSA -keysize 4096 -validity 10000
```

y pásalo en variables de entorno al construir (en GitHub, como *secrets* del repositorio con los
mismos nombres):

| Variable | Contenido |
| --- | --- |
| `BEATSHELL_KEYSTORE_BASE64` | el `.jks` en base64 (`base64 -i beatshell.jks`) |
| `BEATSHELL_KEYSTORE_PASSWORD` | contraseña del keystore |
| `BEATSHELL_KEY_ALIAS` | alias de la clave (`beatshell`) |
| `BEATSHELL_KEY_PASSWORD` | contraseña de la clave (si es distinta) |

Sin esas variables se usa la clave de depuración del SDK.

Para probarlo en un emulador o en un teléfono conectado por USB:

```bash
./gradlew installDebug
```

El workflow [`.github/workflows/android.yml`](../.github/workflows/android.yml) construye los dos
APK en cada PR que toca `android/` o el núcleo, y en cada etiqueta `android-v*` crea un borrador de
release con el APK.

## Cómo está hecho

- **Interfaz**: Kotlin y Jetpack Compose (Material 3 con los colores de
  `desktop/beatshell_desktop/style.py`).
- **Audio**: [Media3](https://developer.android.com/media/media3) — ExoPlayer dentro de un
  `MediaSessionService` en primer plano de tipo `mediaPlayback`. Media3 gestiona la notificación,
  la pantalla de bloqueo, los botones multimedia y el *audio focus*; además se pausa al desconectar
  los auriculares (`setHandleAudioBecomingNoisy`) y mantiene CPU y Wi-Fi despiertos con la pantalla
  apagada (`WAKE_MODE_NETWORK`). Al quitar la app de Recientes el servicio solo se para si no está
  sonando nada. El botón de play de unos auriculares Bluetooth con la app cerrada retoma la última cola.
- **YouTube y Spotify**: el mismo núcleo Python de BeatShell (`src/BeatShell/sources`,
  `models.py`) dentro del APK con [Chaquopy](https://chaquo.com/chaquopy/): búsqueda, playlists,
  lectura de Spotify y emparejado de canciones son exactamente los de escritorio. El puente está en
  [`app/src/main/python/beatshell_android.py`](app/src/main/python/beatshell_android.py).
- **Streams sin motor de JavaScript**: Android no tiene deno/node, así que se usan clientes de
  YouTube que no lo necesitan (comprobado con yt-dlp 2026.11.23):
  - `android_music` / `ios_music`: audio puro M4A (AAC 128 kbps), pero YouTube obliga a esperar
    unos 5 s antes de servirlo (antes responde 403).
  - `android`: solo el formato 18 (MP4 360p con AAC 96 kbps), sin espera.

  La canción que el usuario pulsa empieza al momento con el formato 18 (se descarta el vídeo, solo
  se decodifica el audio) y las siguientes de la cola se resuelven por adelantado en calidad alta,
  así que la espera no se nota. Se puede desactivar en *Ajustes → Arranque instantáneo* para usar
  siempre calidad alta (y menos datos). Si YouTube cambia algo, *Ajustes → Actualizar yt-dlp*
  descarga la última versión de PyPI (comprobando su SHA-256) sin reinstalar la app.

### Licencias de las dependencias

BeatShell es GPL-2.0-only, así que no se usa nada GPL-3.0 (por ejemplo NewPipeExtractor).
- yt-dlp: Unlicense (dominio público). Chaquopy: MIT. mutagen: GPL-2.0-or-later.
- Media3, Jetpack Compose, AndroidX, Kotlin y Coil: Apache-2.0, como todo el SDK de Android.
