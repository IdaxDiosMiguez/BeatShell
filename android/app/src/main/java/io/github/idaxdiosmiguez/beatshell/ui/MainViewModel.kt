package io.github.idaxdiosmiguez.beatshell.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.idaxdiosmiguez.beatshell.core.BeatShellError
import io.github.idaxdiosmiguez.beatshell.core.Collection
import io.github.idaxdiosmiguez.beatshell.core.KIND_ALBUM
import io.github.idaxdiosmiguez.beatshell.core.KIND_PLAYLIST
import io.github.idaxdiosmiguez.beatshell.core.Library
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.download.Downloads
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private val PROFILE_RE = Regex("""open\.spotify\.com/(?:intl-[a-z-]+/)?user/|spotify:user:""")

fun isSpotifyProfile(text: String) = PROFILE_RE.containsMatchIn(text)

fun isSpotifyLink(text: String) = "open.spotify.com/" in text || text.trim().startsWith("spotify:")

data class SearchUi(
    val loading: Boolean = false,
    val loadingText: String = "",
    val results: Collection? = null,
    val error: String? = null,
)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    val library = Library.get(app)
    val player = PlayerController(app)

    /** Texto de la barra de búsqueda (también se rellena con los enlaces compartidos). */
    val query = MutableStateFlow("")

    private val _search = MutableStateFlow(SearchUi())
    val search: StateFlow<SearchUi> = _search.asStateFlow()

    private val _local = MutableStateFlow<List<Track>?>(null)
    val local: StateFlow<List<Track>?> = _local.asStateFlow()

    private val _toasts = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val toasts: SharedFlow<String> = _toasts

    private var searchJob: Job? = null

    init {
        player.connect()
        viewModelScope.launch { player.messages.collect { _toasts.emit(it) } }
    }

    override fun onCleared() {
        player.release()
    }

    fun toast(text: String) {
        _toasts.tryEmit(text)
    }

    // ------------------------------------------------------------------ buscar / abrir enlaces
    /** Texto de la barra o compartido desde otra app: búsqueda o enlace de YouTube/Spotify. */
    fun submit(text: String, autoplay: Boolean = false) {
        val t = text.trim()
        if (t.isEmpty()) return
        if (isSpotifyProfile(t)) { importSpotify(t); return }  // un perfil se importa, no se reproduce
        query.value = t
        searchJob?.cancel()
        val isLink = t.contains("://") || t.startsWith("spotify:")
        val label = when {
            "spotify" in t -> "Leyendo Spotify…"
            isLink && "list=" in t -> "Abriendo la playlist…"
            isLink -> "Abriendo el enlace…"
            else -> "Buscando «$t»…"
        }
        _search.value = SearchUi(loading = true, loadingText = label, results = _search.value.results)
        searchJob = viewModelScope.launch {
            try {
                val col = if (isLink) Py.resolve(t) else Py.search(t)
                _search.value = SearchUi(results = col)
                if (col.tracks.isEmpty()) {
                    _search.value = SearchUi(results = col, error = "Sin resultados")
                } else if (autoplay || col.kind == "video") {
                    if (col.kind == "video") player.playNow(col.tracks[0]) else player.playList(col.tracks)
                }
            } catch (e: BeatShellError) {
                _search.value = SearchUi(error = e.message, results = null)
            }
        }
    }

    // ------------------------------------------------------------------ importar de Spotify
    /** Texto de la importación en curso (null si no hay ninguna). */
    private val _importing = MutableStateFlow<String?>(null)
    val importing: StateFlow<String?> = _importing.asStateFlow()

    /** Id del elemento importado para abrirlo ("" = abrir la biblioteca). */
    val imported = MutableSharedFlow<String>(extraBufferCapacity = 1)

    fun importSpotify(link: String) {
        if (_importing.value != null) { toast("Ya hay una importación en curso"); return }
        val user = isSpotifyProfile(link)
        _importing.value = if (user) "Leyendo el perfil de Spotify…" else "Leyendo Spotify…"
        imported.tryEmit("")  // a la biblioteca, donde se ve el progreso
        viewModelScope.launch {
            try {
                val res = Py.importSpotify(link) { _, label -> _importing.value = "Importando $label" }
                val (_, updated) = library.importCollections(res.collections)
                val prof = res.profile
                val msg = if (prof == null) {
                    val col = res.collections.first()
                    "«${col.name}» ${if (updated > 0) "actualizada" else "importada"}: ${col.tracks.size} canciones"
                } else buildString {
                    val n = res.collections.size
                    append("$n playlist${if (n == 1) "" else "s"} de ${prof.name} importadas")
                    if (updated > 0) append(" ($updated actualizadas)")
                    if (prof.total > prof.read) append(". Tiene ${prof.total}: Spotify solo deja leer las ${prof.read} primeras sin cuenta")
                    if (res.failed.isNotEmpty()) append(". No se pudieron leer: ${res.failed.joinToString()}")
                }
                toast(msg)
                // Una playlist se abre directamente; un perfil lleva a la biblioteca ("").
                imported.tryEmit(if (prof != null) "" else
                    library.playlists.value.firstOrNull { it.sourceUrl == res.collections.first().url }?.id ?: "")
            } catch (e: BeatShellError) {
                toast("No se pudo importar: ${e.message}")
            } finally {
                _importing.value = null
            }
        }
    }

    fun playResult(col: Collection, index: Int) {
        if (col.kind == "search" || col.kind == "video") player.playNow(col.tracks[index]) else player.playList(col.tracks, index)
    }

    fun saveCollection(col: Collection) {
        if (library.findSaved(col) != null) {
            toast("Ya está en tu biblioteca"); return
        }
        val kind = if (col.kind == "album") KIND_ALBUM else KIND_PLAYLIST
        val p = library.create(col.name, col.tracks, kind = kind, sourceUrl = col.url,
            artist = if (kind == KIND_ALBUM) col.owner else "", thumb = col.thumb)
        toast("«${p.name}» guardada en tu biblioteca")
    }

    // ------------------------------------------------------------------ cola
    fun enqueue(tracks: List<Track>, next: Boolean) {
        player.enqueue(tracks, next)
        val what = if (tracks.size == 1) "«${tracks[0].title}»" else "${tracks.size} canciones"
        toast(if (next) "$what a continuación" else "$what en la cola")
    }

    // ------------------------------------------------------------------ playlists
    fun addToPlaylist(id: String, tracks: List<Track>) {
        val p = library.playlist(id) ?: return
        val n = library.addTo(id, tracks)
        toast(if (n == 0) "Ya estaba en «${p.name}»" else "Añadida${if (n > 1) "s $n canciones" else ""} a «${p.name}»")
    }

    fun newPlaylist(name: String, tracks: List<Track> = emptyList()) {
        val p = library.create(name, tracks)
        toast("Playlist «${p.name}» creada")
    }

    // ------------------------------------------------------------------ descargas
    fun download(tracks: List<Track>) {
        val n = Downloads.enqueue(getApplication(), tracks)
        toast(when (n) {
            0 -> "Ya está en descargas"
            1 -> "Descargando «${tracks.first { !it.isLocal }.title}»"
            else -> "Descargando $n canciones"
        })
    }

    // ------------------------------------------------------------------ música local
    fun loadLocal() {
        viewModelScope.launch {
            _local.value = runCatching { library.localTracks() }.getOrElse { emptyList() }
        }
    }

    // ------------------------------------------------------------------ yt-dlp
    fun updateYtdlp() {
        toast("Buscando una versión nueva de yt-dlp…")
        viewModelScope.launch {
            try {
                val (v, updated) = Py.updateYtdlp()
                toast(if (updated) "yt-dlp $v descargado: se usará al volver a abrir la app" else "yt-dlp ya está al día ($v)")
            } catch (e: BeatShellError) {
                toast("No se pudo actualizar: ${e.message}")
            }
        }
    }
}
