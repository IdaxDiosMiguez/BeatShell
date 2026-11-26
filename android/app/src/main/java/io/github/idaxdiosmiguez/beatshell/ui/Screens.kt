package io.github.idaxdiosmiguez.beatshell.ui

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.idaxdiosmiguez.beatshell.BuildConfig
import io.github.idaxdiosmiguez.beatshell.core.Collection
import io.github.idaxdiosmiguez.beatshell.core.KIND_ALBUM
import io.github.idaxdiosmiguez.beatshell.core.Playlist
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.download.DownloadState
import io.github.idaxdiosmiguez.beatshell.download.Downloads

fun humanDuration(tracks: List<Track>): String {
    val total = tracks.sumOf { it.duration ?: 0.0 }.toLong()
    val h = total / 3600
    val m = (total % 3600) / 60
    return when {
        h > 0 -> "$h h $m min"
        m > 0 -> "$m min"
        else -> ""
    }
}

fun summary(tracks: List<Track>): String {
    val n = tracks.size
    val d = humanDuration(tracks)
    return "$n canci${if (n == 1) "ón" else "ones"}" + if (d.isNotEmpty()) " · $d" else ""
}

/** Acciones comunes del menú de una canción. */
class TrackActions(
    val vm: MainViewModel,
    val addToPlaylist: (List<Track>) -> Unit,
) {
    fun common(t: Track): List<MenuAction> = buildList {
        add(MenuAction("Reproducir a continuación") { vm.enqueue(listOf(t), next = true) })
        add(MenuAction("Añadir a la cola") { vm.enqueue(listOf(t), next = false) })
        add(MenuAction("Añadir a playlist…") { addToPlaylist(listOf(t)) })
        if (!t.isLocal) add(MenuAction("Descargar (M4A)") { vm.download(listOf(t)) })
    }
}

// ====================================================================== Buscar
@Composable
fun SearchScreen(vm: MainViewModel, actions: TrackActions, ui: PlayerUi) {
    val c = LocalBs.current
    val state by vm.search.collectAsState()
    val query by vm.query.collectAsState()
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    val context = LocalContext.current
    // Al llegar un enlace compartido (o al buscar) se esconde el teclado.
    LaunchedEffect(state.loading) {
        if (state.loading) {
            keyboard?.hide(); focus.clearFocus()
        }
    }

    fun submit() {
        keyboard?.hide()
        focus.clearFocus()
        vm.submit(query)
    }

    Column(Modifier.fillMaxSize()) {
        TextField(
            value = query,
            onValueChange = { vm.query.value = it },
            placeholder = { Text("Busca o pega un enlace", color = c.faint, maxLines = 1) },
            leadingIcon = { Icon(Icons.Rounded.Search, null, tint = c.muted) },
            trailingIcon = {
                if (query.isNotEmpty()) IconButton(onClick = { vm.query.value = "" }) { Icon(Icons.Rounded.Close, "Borrar", tint = c.muted) }
            },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { submit() }),
            shape = RoundedCornerShape(12.dp),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = c.hover, unfocusedContainerColor = c.hover,
                focusedIndicatorColor = c.hover, unfocusedIndicatorColor = c.hover,
                cursorColor = c.accent, focusedTextColor = c.text, unfocusedTextColor = c.text,
            ),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        if (state.loading) {
            LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp), color = c.accent, trackColor = c.bg)
        } else {
            Spacer(Modifier.height(2.dp))
        }
        val col = state.results
        when {
            state.loading && col == null -> EmptyText(state.loadingText)
            state.error != null && col == null -> Column(Modifier.padding(16.dp)) {
                Text("No se pudo abrir", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold, color = c.text)
                Spacer(Modifier.height(4.dp))
                Text(state.error ?: "", color = c.muted)
            }
            col == null -> SearchWelcome {
                val clip = (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                    .primaryClip?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
                if (clip.isBlank()) vm.toast("El portapapeles está vacío") else vm.submit(clip)
            }
            else -> CollectionView(vm, actions, col, ui, loadingText = if (state.loading) state.loadingText else null)
        }
    }
}

@Composable
private fun SearchWelcome(onPaste: () -> Unit) {
    val c = LocalBs.current
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        LogoMark(Modifier.size(72.dp))
        Spacer(Modifier.height(18.dp))
        Text("Busca lo que quieras escuchar", fontWeight = FontWeight.Bold, fontSize = 17.sp, color = c.text)
        Spacer(Modifier.height(6.dp))
        Text(
            "También puedes pegar o compartir con BeatShell enlaces de YouTube (vídeos y playlists) y de Spotify (canciones, álbumes, playlists y perfiles: se importan sus playlists públicas).",
            color = c.muted, fontSize = 13.sp, textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Spacer(Modifier.height(18.dp))
        PillButton("Pegar enlace", primary = true, onClick = onPaste)
    }
}

@Composable
fun CollectionView(vm: MainViewModel, actions: TrackActions, col: Collection, ui: PlayerUi, loadingText: String? = null) {
    val c = LocalBs.current
    val label = when (col.kind) {
        "album" -> "Álbum"
        "playlist" -> when {
            col.url?.contains("/track/") == true -> "Canción de Spotify"
            col.source == "spotify" -> "Playlist de Spotify"
            else -> "Playlist de YouTube"
        }
        "video" -> "Vídeo de YouTube"
        else -> "Resultados de YouTube"
    }
    TrackList(
        tracks = col.tracks,
        currentUid = null,
        currentKey = ui.current?.key,
        playing = ui.isPlaying,
        onClick = { vm.playResult(col, it) },
        menu = { _, t -> actions.common(t) },
        header = {
            val more: @Composable () -> Unit = {
                var open by remember { mutableStateOf(false) }
                Box {
                    IconButton(onClick = { open = true }) { Icon(Icons.Rounded.MoreVert, "Más", tint = c.muted) }
                    DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                        DropdownMenuItem(text = { Text("Añadir todo a la cola") }, onClick = { open = false; vm.enqueue(col.tracks, false) })
                        DropdownMenuItem(text = { Text("Guardar en biblioteca") }, onClick = { open = false; vm.saveCollection(col) })
                        DropdownMenuItem(text = { Text("Añadir a playlist…") }, onClick = { open = false; actions.addToPlaylist(col.tracks) })
                        DropdownMenuItem(text = { Text("Descargar todo (M4A)") }, onClick = { open = false; vm.download(col.tracks) })
                    }
                }
            }
            if (col.kind == "playlist" || col.kind == "album") {
                CollectionHero(
                    title = col.name,
                    owner = col.owner,
                    info = listOfNotNull(loadingText ?: label, summary(col.tracks)).joinToString(" · "),
                    thumb = col.thumb,
                    tracks = col.tracks,
                    onPlay = { vm.player.playList(col.tracks) },
                    onShuffle = { vm.player.shuffleList(col.tracks) },
                    actions = more,
                )
            } else Column {
                PageHeader(
                    title = if (col.kind == "search") "Resultados" else col.name,
                    subtitle = listOfNotNull(loadingText ?: label, col.owner.takeIf { it.isNotBlank() && col.kind != "search" },
                        summary(col.tracks)).joinToString(" · "),
                ) {
                    PillButton("Reproducir", primary = true) { vm.player.playList(col.tracks) }
                    PillButton("Aleatorio") { vm.player.shuffleList(col.tracks) }
                    var more by remember { mutableStateOf(false) }
                    Box {
                        IconButton(onClick = { more = true }) { Icon(Icons.Rounded.MoreVert, "Más", tint = c.muted) }
                        DropdownMenu(expanded = more, onDismissRequest = { more = false }) {
                            DropdownMenuItem(text = { Text("Añadir todo a la cola") }, onClick = { more = false; vm.enqueue(col.tracks, false) })
                            if (col.kind != "search") {
                                DropdownMenuItem(text = { Text("Guardar en biblioteca") }, onClick = { more = false; vm.saveCollection(col) })
                            }
                            DropdownMenuItem(text = { Text("Añadir a playlist…") }, onClick = { more = false; actions.addToPlaylist(col.tracks) })
                            DropdownMenuItem(text = { Text("Descargar todo (M4A)") }, onClick = { more = false; vm.download(col.tracks) })
                        }
                    }
                }
            }
        },
        empty = "Sin resultados",
    )
}

// ====================================================================== Biblioteca
sealed interface LibPage {
    data object Home : LibPage
    data object Local : LibPage
    data object Downloads : LibPage
    data object Settings : LibPage
    data class Saved(val id: String) : LibPage
}

@Composable
fun LibraryHome(
    vm: MainViewModel,
    open: (LibPage) -> Unit,
    onNewPlaylist: () -> Unit,
    onImport: () -> Unit,
    onExport: () -> Unit,
    onImportSpotify: () -> Unit,
) {
    val c = LocalBs.current
    val playlists by vm.library.playlists.collectAsState()
    val importing by vm.importing.collectAsState()
    val jobs by Downloads.jobs.collectAsState()
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) { PageHeader("Biblioteca") }
                var more by remember { mutableStateOf(false) }
                Box(Modifier.padding(end = 4.dp)) {
                    IconButton(onClick = { more = true }) { Icon(Icons.Rounded.MoreVert, "Más", tint = c.muted) }
                    DropdownMenu(expanded = more, onDismissRequest = { more = false }) {
                        DropdownMenuItem(text = { Text("Nueva playlist") }, onClick = { more = false; onNewPlaylist() })
                        DropdownMenuItem(text = { Text("Importar de Spotify…") }, onClick = { more = false; onImportSpotify() })
                        DropdownMenuItem(text = { Text("Importar playlists.json") }, onClick = { more = false; onImport() })
                        DropdownMenuItem(text = { Text("Exportar playlists.json") }, onClick = { more = false; onExport() })
                    }
                }
            }
        }
        item {
            val busy = importing
            if (busy != null) {
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Text(busy, color = c.text, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(6.dp))
                    LinearProgressIndicator(Modifier.fillMaxWidth(), color = c.accent, trackColor = c.hover)
                }
            } else {
                NavRow(Icons.Rounded.Add, "Importar de Spotify",
                    "Pega el enlace de una playlist o de un perfil", onImportSpotify)
            }
        }
        item { NavRow(Icons.Rounded.MusicNote, "Música del teléfono", "Canciones guardadas en este dispositivo") { open(LibPage.Local) } }
        if (jobs.isNotEmpty()) {
            val active = jobs.count { it.state == DownloadState.RUNNING || it.state == DownloadState.QUEUED }
            item {
                NavRow(Icons.Rounded.Download, "Descargas",
                    if (active > 0) "$active en curso" else "${jobs.size} terminadas") { open(LibPage.Downloads) }
            }
        }
        item { NavRow(Icons.Rounded.Settings, "Ajustes", "Calidad, yt-dlp y versión") { open(LibPage.Settings) } }
        item {
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 18.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text("PLAYLISTS", color = c.faint, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp, modifier = Modifier.weight(1f))
                IconButton(onClick = onNewPlaylist) { Icon(Icons.Rounded.Add, "Nueva playlist", tint = c.muted) }
            }
        }
        val visible = playlists.filterNot { it.isFolder }
        if (visible.isEmpty()) {
            item { EmptyText("Guarda playlists y álbumes desde la búsqueda o crea una nueva") }
        }
        items(visible, key = { it.id }) { p -> PlaylistRow(p) { open(LibPage.Saved(p.id)) } }
    }
}

@Composable
private fun NavRow(icon: ImageVector, title: String, subtitle: String, onClick: () -> Unit) {
    val c = LocalBs.current
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(48.dp).clip(RoundedCornerShape(8.dp)).background(c.hover), contentAlignment = Alignment.Center) {
            Icon(icon, null, tint = c.accent)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.Bold, fontSize = 15.sp, color = c.text)
            Text(subtitle, color = c.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
private fun PlaylistRow(p: Playlist, onClick: () -> Unit) {
    val c = LocalBs.current
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Como en Spotify: portada (o mosaico), nombre y «Playlist · autor»
        MosaicCover(p.image, p.tracks, Modifier.size(64.dp))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(p.name, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, color = c.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(3.dp))
            val kind = if (p.kind == KIND_ALBUM) "Álbum" else "Playlist"
            val who = p.artist.ifBlank { if (p.tracks.size == 1) "1 canción" else "${p.tracks.size} canciones" }
            Text("$kind · $who", color = c.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
fun BackBar(title: String, onBack: () -> Unit, actions: @Composable () -> Unit = {}) {
    val c = LocalBs.current
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Atrás", tint = c.text) }
        Text(title, color = c.muted, fontSize = 14.sp, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        actions()
    }
}

@Composable
fun SavedPlaylistScreen(vm: MainViewModel, actions: TrackActions, id: String, ui: PlayerUi, onBack: () -> Unit) {
    val c = LocalBs.current
    val playlists by vm.library.playlists.collectAsState()
    val p = playlists.firstOrNull { it.id == id }
    if (p == null) {
        LaunchedEffect(Unit) { onBack() }
        return
    }
    var renaming by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    // Selector de fotos del sistema: no necesita permisos.
    val pickPhoto = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) vm.changePhoto(id, uri)
    }
    val choosePhoto = { pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
    Column(Modifier.fillMaxSize()) {
        BackBar("Biblioteca", onBack) {
            var more by remember { mutableStateOf(false) }
            Box {
                IconButton(onClick = { more = true }) { Icon(Icons.Rounded.MoreVert, "Más", tint = c.muted) }
                DropdownMenu(expanded = more, onDismissRequest = { more = false }) {
                    DropdownMenuItem(text = { Text("Añadir todo a la cola") }, onClick = { more = false; vm.enqueue(p.tracks, false) })
                    DropdownMenuItem(text = { Text("Descargar todo (M4A)") }, onClick = { more = false; vm.download(p.tracks) })
                    DropdownMenuItem(text = { Text("Renombrar") }, onClick = { more = false; renaming = true })
                    DropdownMenuItem(text = { Text("Cambiar foto") }, onClick = { more = false; choosePhoto() })
                    if (p.customThumb != null) {
                        DropdownMenuItem(text = { Text("Quitar foto") }, onClick = { more = false; vm.removePhoto(p.id) })
                    }
                    DropdownMenuItem(text = { Text("Eliminar") }, onClick = { more = false; deleting = true })
                }
            }
        }
        TrackList(
            tracks = p.tracks,
            currentUid = null,
            currentKey = ui.current?.key,
            playing = ui.isPlaying,
            onClick = { vm.player.playList(p.tracks, it) },
            menu = { i, t ->
                actions.common(t) + listOfNotNull(
                    MenuAction("Subir") { vm.library.moveIn(p.id, i, -1) }.takeIf { i > 0 },
                    MenuAction("Bajar") { vm.library.moveIn(p.id, i, 1) }.takeIf { i < p.tracks.lastIndex },
                    MenuAction("Quitar de la playlist") { vm.library.removeFrom(p.id, t.uid) },
                )
            },
            header = {
                val spotify = p.sourceUrl?.contains("spotify.com") == true
                CollectionHero(
                    title = p.name,
                    owner = p.artist,
                    info = listOf(if (p.kind == KIND_ALBUM) "Álbum" else if (spotify) "Playlist pública" else "Playlist",
                        summary(p.tracks)).joinToString(" · "),
                    thumb = p.image,
                    tracks = p.tracks,
                    onPlay = { vm.player.playList(p.tracks) },
                    onShuffle = { vm.player.shuffleList(p.tracks) },
                    onCoverClick = { choosePhoto() },
                )
            },
            empty = "Esta playlist está vacía. Añade canciones desde su menú «⋮».",
        )
    }
    if (renaming) {
        TextInputDialog("Renombrar", p.name, "Guardar", { renaming = false }) { vm.library.rename(p.id, it); renaming = false }
    }
    if (deleting) {
        ConfirmDialog("Eliminar «${p.name}»", "Se borrará de tu biblioteca. Las canciones descargadas no se tocan.", "Eliminar",
            { deleting = false }) { deleting = false; vm.library.delete(p.id); onBack() }
    }
}

@Composable
fun LocalScreen(vm: MainViewModel, actions: TrackActions, ui: PlayerUi, hasPermission: Boolean, requestPermission: () -> Unit, onBack: () -> Unit) {
    val tracks by vm.local.collectAsState()
    var filter by rememberSaveable { mutableStateOf("") }
    val c = LocalBs.current
    LaunchedEffect(hasPermission) { if (hasPermission) vm.loadLocal() }
    Column(Modifier.fillMaxSize()) {
        BackBar("Biblioteca", onBack)
        if (!hasPermission) {
            PageHeader("Música del teléfono", "BeatShell necesita permiso para leer tu música.") {
                PillButton("Dar permiso", primary = true, onClick = requestPermission)
            }
            return
        }
        val all = tracks
        val shown = remember(all, filter) {
            if (filter.isBlank() || all == null) all.orEmpty() else {
                val f = filter.lowercase()
                all.filter { f in it.title.lowercase() || f in it.artist.lowercase() || f in it.album.lowercase() }
            }
        }
        TrackList(
            tracks = shown,
            currentUid = null,
            currentKey = ui.current?.key,
            playing = ui.isPlaying,
            onClick = { vm.player.playList(shown, it) },
            menu = { _, t -> actions.common(t) },
            header = {
                Column {
                    PageHeader("Música del teléfono", if (all == null) "Leyendo tu música…" else summary(all)) {
                        PillButton("Reproducir", primary = true) { vm.player.playList(shown) }
                        PillButton("Aleatorio") { vm.player.shuffleList(shown) }
                    }
                    TextField(
                        value = filter, onValueChange = { filter = it }, singleLine = true,
                        placeholder = { Text("Filtrar", color = c.faint) },
                        leadingIcon = { Icon(Icons.Rounded.Search, null, tint = c.faint) },
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = c.bg, unfocusedContainerColor = c.bg,
                            focusedIndicatorColor = c.accent, unfocusedIndicatorColor = c.border, cursorColor = c.accent,
                        ),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                    )
                }
            },
            empty = if (all == null) "Leyendo tu música…" else "No hay canciones en el teléfono",
        )
    }
}

@Composable
fun DownloadsScreen(onBack: () -> Unit) {
    val c = LocalBs.current
    val jobs by Downloads.jobs.collectAsState()
    val context = LocalContext.current
    Column(Modifier.fillMaxSize()) {
        BackBar("Biblioteca", onBack)
        LazyColumn(Modifier.fillMaxSize()) {
            item {
                PageHeader("Descargas", "Se guardan en Música/BeatShell en formato M4A") {
                    if (jobs.any { it.state == DownloadState.FAILED }) PillButton("Reintentar fallidas") { Downloads.retryFailed(context) }
                    if (jobs.any { it.state == DownloadState.DONE || it.state == DownloadState.FAILED }) PillButton("Limpiar terminadas") { Downloads.clearFinished() }
                }
            }
            if (jobs.isEmpty()) item { EmptyText("No hay descargas") }
            items(jobs, key = { it.id }) { j ->
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(j.track.title, fontWeight = FontWeight.Bold, fontSize = 15.sp, color = c.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(j.error ?: j.track.displayArtist, color = if (j.error != null) c.error else c.muted, fontSize = 13.sp,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        Text(
                            when (j.state) {
                                DownloadState.RUNNING -> "${(j.progress * 100).toInt()} %"
                                else -> j.label
                            },
                            color = if (j.state == DownloadState.DONE) c.accent else c.faint, fontSize = 12.sp,
                        )
                    }
                    if (j.state == DownloadState.RUNNING) {
                        Spacer(Modifier.height(6.dp))
                        LinearProgressIndicator(progress = { j.progress }, modifier = Modifier.fillMaxWidth().height(3.dp),
                            color = c.accent, trackColor = c.selected)
                    }
                }
            }
        }
    }
}

@Composable
fun SettingsScreen(vm: MainViewModel, onBack: () -> Unit) {
    val c = LocalBs.current
    var fast by remember { mutableStateOf(vm.library.fastStart) }
    Column(Modifier.fillMaxSize()) {
        BackBar("Biblioteca", onBack)
        PageHeader("Ajustes")
        Row(Modifier.fillMaxWidth().clickable { fast = !fast; vm.library.fastStart = fast }.padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Arranque instantáneo", fontWeight = FontWeight.Bold, fontSize = 15.sp, color = c.text)
                Text("La canción que eliges empieza al momento con un formato de YouTube que gasta más datos. " +
                    "Sin esto tarda unos 5 s en empezar, pero siempre en calidad alta (AAC 128 kbps). " +
                    "Las siguientes de la cola siempre van en calidad alta.",
                    color = c.muted, fontSize = 13.sp)
            }
            Spacer(Modifier.width(12.dp))
            Switch(checked = fast, onCheckedChange = { fast = it; vm.library.fastStart = it },
                colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.accentText))
        }
        Row(Modifier.fillMaxWidth().clickable { vm.updateYtdlp() }.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Column {
                Text("Actualizar yt-dlp", fontWeight = FontWeight.Bold, fontSize = 15.sp, color = c.text)
                Text("Versión ${Py.ytdlpVersion}. Si YouTube deja de funcionar, descarga la última versión desde PyPI.",
                    color = c.muted, fontSize = 13.sp)
            }
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
            Text("BeatShell ${BuildConfig.VERSION_NAME}", fontWeight = FontWeight.Bold, fontSize = 15.sp, color = c.text)
            Text("Software libre (GPL-2.0). github.com/IdaxDiosMiguez/BeatShell", color = c.muted, fontSize = 13.sp)
        }
    }
}

// ====================================================================== Cola
@Composable
fun QueueScreen(vm: MainViewModel, actions: TrackActions, ui: PlayerUi) {
    TrackList(
        tracks = ui.queue,
        currentUid = null,
        currentKey = null,
        currentIndex = ui.currentIndex,
        playing = ui.isPlaying,
        onClick = { vm.player.jump(it) },
        menu = { i, t ->
            listOfNotNull(
                MenuAction("Quitar de la cola") { vm.player.remove(i) },
                MenuAction("Subir") { vm.player.move(i, i - 1) }.takeIf { i > 0 },
                MenuAction("Bajar") { vm.player.move(i, i + 1) }.takeIf { i < ui.queue.lastIndex },
                MenuAction("Añadir a playlist…") { actions.addToPlaylist(listOf(t)) },
                MenuAction("Descargar (M4A)") { vm.download(listOf(t)) }.takeIf { !t.isLocal },
            )
        },
        header = {
            PageHeader("Cola", if (ui.queue.isEmpty()) null else summary(ui.queue) +
                (if (ui.shuffle) " · aleatorio" else "")) {
                if (ui.queue.size > 1) PillButton("Vaciar cola") { vm.player.clearQueue() }
                if (ui.queue.isNotEmpty()) PillButton("Guardar como playlist") { actions.addToPlaylist(ui.queue) }
            }
        },
        empty = "La cola está vacía. Busca algo para empezar.",
    )
}
