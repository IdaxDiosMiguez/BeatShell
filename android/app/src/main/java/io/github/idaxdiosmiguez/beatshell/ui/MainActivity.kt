package io.github.idaxdiosmiguez.beatshell.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.LibraryMusic
import androidx.compose.material.icons.automirrored.rounded.QueueMusic
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.Track

enum class Tab(val label: String) { SEARCH("Buscar"), LIBRARY("Biblioteca"), QUEUE("Cola") }

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()
    private val pendingIntent = MutableStateFlow<Intent?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) pendingIntent.value = intent
        setContent {
            BeatShellTheme { App(vm, pendingIntent) }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingIntent.value = intent
    }

    companion object {
        const val ACTION_OPEN_PLAYER = "io.github.idaxdiosmiguez.beatshell.OPEN_PLAYER"
        const val ACTION_OPEN_DOWNLOADS = "io.github.idaxdiosmiguez.beatshell.OPEN_DOWNLOADS"
    }
}

/** Texto o enlace que otra app ha compartido o abierto con BeatShell. */
private fun sharedText(intent: Intent): String? = when (intent.action) {
    Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT) ?: intent.getStringExtra(Intent.EXTRA_SUBJECT)
    Intent.ACTION_VIEW -> intent.dataString
    else -> null
}?.takeIf { it.isNotBlank() }

private val audioPermission =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) Manifest.permission.READ_MEDIA_AUDIO
    else Manifest.permission.READ_EXTERNAL_STORAGE

@Composable
fun App(vm: MainViewModel, intents: MutableStateFlow<Intent?>) {
    val c = LocalBs.current
    val context = LocalContext.current
    val ui by vm.player.state.collectAsState()
    var tab by rememberSaveable { mutableStateOf(Tab.SEARCH) }
    var libPage by remember { mutableStateOf<LibPage>(LibPage.Home) }
    var expanded by rememberSaveable { mutableStateOf(false) }
    var addTracks by remember { mutableStateOf<List<Track>?>(null) }
    var newPlaylistFor by remember { mutableStateOf<List<Track>?>(null) }
    var spotifyImport by remember { mutableStateOf<String?>(null) }  // texto inicial del diálogo
    LaunchedEffect(Unit) {
        // Al importar una sola playlist, se abre en la biblioteca.
        vm.imported.collect { id ->
            tab = Tab.LIBRARY; expanded = false
            libPage = if (id.isEmpty()) LibPage.Home else LibPage.Saved(id)
        }
    }
    val snackbar = remember { SnackbarHostState() }
    val actions = remember(vm) { TrackActions(vm) { addTracks = it } }

    // ------------------------------------------------------------------ permisos
    var hasAudio by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, audioPermission) == PackageManager.PERMISSION_GRANTED)
    }
    val audioLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { hasAudio = it }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        // Android 13+: sin este permiso no se ve la notificación con los controles.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // ------------------------------------------------------------------ importar / exportar
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val exportLauncher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                withContext(Dispatchers.IO) {
                    context.contentResolver.openOutputStream(uri)!!.use { it.write(vm.library.exportJson().toByteArray()) }
                }
            }.onSuccess { vm.toast("Playlists exportadas") }.onFailure { vm.toast("No se pudo exportar") }
        }
    }
    val importLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val text = withContext(Dispatchers.IO) { context.contentResolver.openInputStream(uri)!!.use { it.reader().readText() } }
                vm.library.importJson(text)
            }.onSuccess { vm.toast("$it playlist${if (it == 1) "" else "s"} importada${if (it == 1) "" else "s"}") }
                .onFailure { vm.toast(it.message ?: "No se pudo importar") }
        }
    }

    // ------------------------------------------------------------------ intents
    val pending by intents.collectAsState()
    LaunchedEffect(pending) {
        val intent = pending ?: return@LaunchedEffect
        intents.value = null
        when (intent.action) {
            MainActivity.ACTION_OPEN_PLAYER -> expanded = true
            MainActivity.ACTION_OPEN_DOWNLOADS -> { tab = Tab.LIBRARY; libPage = LibPage.Downloads; expanded = false }
            else -> sharedText(intent)?.let {
                tab = Tab.SEARCH; expanded = false
                vm.submit(it)
            }
        }
    }

    LaunchedEffect(Unit) { vm.toasts.collect { snackbar.currentSnackbarData?.dismiss(); snackbar.showSnackbar(it) } }

    BackHandler(enabled = expanded) { expanded = false }
    BackHandler(enabled = !expanded && tab == Tab.LIBRARY && libPage != LibPage.Home) { libPage = LibPage.Home }
    BackHandler(enabled = !expanded && tab != Tab.SEARCH && !(tab == Tab.LIBRARY && libPage != LibPage.Home)) { tab = Tab.SEARCH }

    Box(Modifier.fillMaxSize().background(c.bg)) {
        Scaffold(
            containerColor = c.bg,
            snackbarHost = {
                SnackbarHost(snackbar) { Snackbar(it, containerColor = c.text, contentColor = c.bg) }
            },
            bottomBar = {
                Column {
                    MiniPlayer(ui, vm.player) { expanded = true }
                    NavigationBar(containerColor = c.side, tonalElevation = androidx.compose.ui.unit.Dp(0f)) {
                        Tab.entries.forEach { t ->
                            NavigationBarItem(
                                selected = tab == t,
                                onClick = {
                                    if (tab == t && t == Tab.LIBRARY) libPage = LibPage.Home
                                    tab = t
                                },
                                icon = {
                                    Icon(when (t) {
                                        Tab.SEARCH -> Icons.Rounded.Search
                                        Tab.LIBRARY -> Icons.Rounded.LibraryMusic
                                        Tab.QUEUE -> Icons.AutoMirrored.Rounded.QueueMusic
                                    }, contentDescription = t.label)
                                },
                                label = { Text(t.label, fontWeight = FontWeight.SemiBold) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedIconColor = c.text, selectedTextColor = c.text, indicatorColor = c.selected,
                                    unselectedIconColor = c.muted, unselectedTextColor = c.muted,
                                ),
                            )
                        }
                    }
                }
            },
        ) { padding ->
            Box(Modifier.padding(padding).fillMaxSize()) {
                when (tab) {
                    Tab.SEARCH -> SearchScreen(vm, actions, ui)
                    Tab.QUEUE -> QueueScreen(vm, actions, ui)
                    Tab.LIBRARY -> when (val p = libPage) {
                        LibPage.Home -> LibraryHome(vm, { libPage = it }, { newPlaylistFor = emptyList() },
                            { importLauncher.launch(arrayOf("application/json", "text/plain", "*/*")) },
                            { exportLauncher.launch("playlists.json") },
                            {
                                val clip = (context.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager)
                                    .primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
                                spotifyImport = Py.extractLink(clip)?.takeIf { isSpotifyLink(it) }.orEmpty()
                            })
                        LibPage.Local -> LocalScreen(vm, actions, ui, hasAudio, { audioLauncher.launch(audioPermission) }) { libPage = LibPage.Home }
                        LibPage.Downloads -> DownloadsScreen { libPage = LibPage.Home }
                        LibPage.Settings -> SettingsScreen(vm) { libPage = LibPage.Home }
                        is LibPage.Saved -> SavedPlaylistScreen(vm, actions, p.id, ui) { libPage = LibPage.Home }
                    }
                }
            }
        }
        AnimatedVisibility(
            visible = expanded && ui.current != null,
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
        ) {
            FullPlayer(ui, vm.player, onCollapse = { expanded = false }, onQueue = { expanded = false; tab = Tab.QUEUE })
        }
    }

    // ------------------------------------------------------------------ diálogos
    addTracks?.let { tracks ->
        val playlists by vm.library.playlists.collectAsState()
        AlertDialog(
            onDismissRequest = { addTracks = null },
            title = { Text("Añadir a playlist") },
            text = {
                LazyColumn {
                    item {
                        TextButton(onClick = { newPlaylistFor = tracks; addTracks = null }) { Text("+ Nueva playlist", color = c.accent) }
                    }
                    items(playlists.filterNot { it.isFolder }, key = { it.id }) { p ->
                        TextButton(onClick = { vm.addToPlaylist(p.id, tracks); addTracks = null }) {
                            Text(p.name, color = c.text)
                        }
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { addTracks = null }) { Text("Cancelar") } },
            containerColor = c.selected,
        )
    }
    spotifyImport?.let { initial ->
        TextInputDialog(
            "Importar de Spotify", initial, "Importar", { spotifyImport = null },
            hint = "Enlace de una playlist, un álbum o un perfil. Con un perfil se importan todas sus playlists públicas.",
            placeholder = "https://open.spotify.com/…",
        ) {
            spotifyImport = null
            if (isSpotifyLink(it)) vm.importSpotify(it) else vm.toast("Eso no es un enlace de Spotify")
        }
    }
    newPlaylistFor?.let { tracks ->
        TextInputDialog("Nueva playlist", "", "Crear", { newPlaylistFor = null }) {
            vm.newPlaylist(it, tracks); newPlaylistFor = null
        }
    }
}
