package io.github.idaxdiosmiguez.beatshell.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil3.compose.AsyncImage
import coil3.compose.rememberAsyncImagePainter
import io.github.idaxdiosmiguez.beatshell.core.Track
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.foundation.clickable
import androidx.compose.material.icons.rounded.Shuffle
import io.github.idaxdiosmiguez.beatshell.core.fmtTime

/** Una acción del menú de una canción. */
data class MenuAction(val label: String, val run: () -> Unit)

/** El logo ▚: bloque rosa arriba a la izquierda y cian abajo a la derecha. */
@Composable
fun LogoMark(modifier: Modifier = Modifier, background: Boolean = true) {
    Canvas(modifier) {
        val s = size.minDimension
        if (background) {
            drawRoundRect(
                Brush.verticalGradient(listOf(Color(0xFF1C1630), Color(0xFF0C0A14))),
                size = Size(s, s), cornerRadius = CornerRadius(s * 0.22f),
            )
        }
        val cell = s * 0.3f
        val gap = s * 0.02f
        val c = s / 2
        val r = CornerRadius(cell * 0.12f)
        drawRoundRect(Color(0xFFFF4FD8), Offset(c - gap / 2 - cell, c - gap / 2 - cell), Size(cell, cell), r)
        drawRoundRect(Cyan, Offset(c + gap / 2, c + gap / 2), Size(cell, cell), r)
    }
}

/** Portada con el logo de respaldo mientras carga o si no hay. */
@Composable
fun Cover(url: String?, modifier: Modifier, radius: Dp = 8.dp, fallbackUrl: String? = null) {
    Box(modifier.clip(RoundedCornerShape(radius)).background(LocalBs.current.hover)) {
        LogoMark(Modifier.fillMaxSize())
        if (url != null) {
            AsyncImage(
                model = url,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                error = fallbackUrl?.let { rememberAsyncImagePainter(it) },
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

/** Barras de ecualizador animadas para la canción que suena (como en escritorio). */
@Composable
fun EqBars(playing: Boolean, modifier: Modifier = Modifier) {
    val color = LocalBs.current.accent
    val t = rememberInfiniteTransition(label = "eq")
    val phases = (0 until 3).map { i ->
        t.animateFloat(
            initialValue = 0.3f, targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(420 + i * 130), RepeatMode.Reverse), label = "b$i",
        )
    }
    Canvas(modifier.size(16.dp, 14.dp)) {
        val w = size.width / 5
        phases.forEachIndexed { i, p ->
            val h = size.height * (if (playing) p.value else listOf(0.45f, 0.8f, 0.6f)[i])
            drawRoundRect(color, Offset(i * 2 * w, size.height - h), Size(w, h), CornerRadius(w / 3))
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun TrackRow(
    track: Track,
    index: Int,
    current: Boolean,
    playing: Boolean,
    onClick: () -> Unit,
    menu: List<MenuAction>,
    dim: Boolean = false,
) {
    val c = LocalBs.current
    var open by remember { mutableStateOf(false) }
    Row(
        Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = onClick, onLongClick = { open = true })
            .padding(start = 4.dp, end = 0.dp)
            .height(64.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Portada de la canción (como en Spotify); la que suena lleva las barras encima.
        Box(Modifier.padding(start = 12.dp, end = 12.dp).size(46.dp), contentAlignment = Alignment.Center) {
            Cover(track.cover, Modifier.fillMaxSize(), radius = 4.dp)
            if (current) {
                Box(Modifier.fillMaxSize().clip(RoundedCornerShape(4.dp)).background(Color(0x99000000)),
                    contentAlignment = Alignment.Center) { EqBars(playing) }
            }
        }
        Column(Modifier.weight(1f).padding(end = 8.dp), verticalArrangement = Arrangement.Center) {
            Text(
                track.title, fontWeight = FontWeight.Bold, fontSize = 15.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis, color = if (current) c.accent else if (dim) c.muted else c.text,
            )
            Spacer(Modifier.height(2.dp))
            Text(track.displayArtist, color = c.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Text(fmtTime(track.duration), color = c.faint, fontSize = 12.sp)
        Box {
            IconButton(onClick = { open = true }) {
                Icon(Icons.Rounded.MoreVert, contentDescription = "Opciones", tint = c.faint)
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                menu.forEach { a ->
                    DropdownMenuItem(text = { Text(a.label) }, onClick = { open = false; a.run() })
                }
            }
        }
    }
}

@Composable
fun TrackList(
    tracks: List<Track>,
    currentUid: String?,
    currentKey: String?,
    playing: Boolean,
    onClick: (Int) -> Unit,
    menu: (Int, Track) -> List<MenuAction>,
    header: (@Composable () -> Unit)? = null,
    empty: String = "Nada por aquí",
    contentPadding: PaddingValues = PaddingValues(bottom = 16.dp),
    currentIndex: Int = -1,
) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = contentPadding) {
        header?.let { item { it() } }
        if (tracks.isEmpty()) {
            item { EmptyText(empty) }
        }
        items(tracks.size, key = { "${tracks[it].uid}-$it" }) { i ->
            val t = tracks[i]
            val isCurrent = if (currentIndex >= 0) i == currentIndex else
                (t.uid == currentUid || (currentKey != null && t.key == currentKey))
            TrackRow(t, i, isCurrent, playing, { onClick(i) }, menu(i, t),
                dim = currentIndex >= 0 && i < currentIndex)
        }
    }
}

@Composable
fun EmptyText(text: String) {
    Box(Modifier.fillMaxWidth().padding(vertical = 64.dp, horizontal = 32.dp), contentAlignment = Alignment.Center) {
        Text(text, color = LocalBs.current.faint, fontSize = 14.sp)
    }
}

/**
 * Portada de una playlist como en Spotify: la suya, o un mosaico 2×2 con las portadas de sus
 * cuatro primeras canciones distintas.
 */
@Composable
fun MosaicCover(thumb: String?, tracks: List<Track>, modifier: Modifier, radius: Dp = 4.dp) {
    val tiles = remember(thumb, tracks) {
        if (thumb != null) emptyList() else tracks.mapNotNull { it.cover }.distinct().take(4)
    }
    if (thumb != null || tiles.size < 4) {
        Cover(thumb ?: tiles.firstOrNull(), modifier, radius)
        return
    }
    Column(modifier.clip(RoundedCornerShape(radius)).background(LocalBs.current.hover)) {
        for (r in 0..1) Row(Modifier.weight(1f)) {
            for (k in 0..1) Cover(tiles[r * 2 + k], Modifier.weight(1f).fillMaxSize(), 0.dp)
        }
    }
}

/**
 * Cabecera de playlist/álbum al estilo Spotify: degradado de fondo, portada grande centrada,
 * nombre, autor, tipo y duración, y a la derecha aleatorio + botón de reproducir redondo.
 */
@Composable
fun CollectionHero(
    title: String,
    owner: String,
    info: String,
    thumb: String?,
    tracks: List<Track>,
    onPlay: () -> Unit,
    onShuffle: () -> Unit,
    actions: @Composable () -> Unit = {},
    onCoverClick: (() -> Unit)? = null,
) {
    val c = LocalBs.current
    Column(
        Modifier.fillMaxWidth()
            .background(Brush.verticalGradient(listOf(c.accent.copy(alpha = 0.28f), Color.Transparent)))
            .padding(top = 16.dp),
    ) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Box(if (onCoverClick != null) Modifier.clickable(onClick = onCoverClick) else Modifier) {
                MosaicCover(thumb, tracks, Modifier.size(232.dp), radius = 6.dp)
                if (onCoverClick != null) {
                    // Lápiz discreto: la portada se puede tocar para cambiar la foto.
                    Box(Modifier.align(Alignment.BottomEnd).padding(8.dp).size(34.dp).clip(CircleShape)
                        .background(Color(0x99000000)), contentAlignment = Alignment.Center) {
                        Icon(Icons.Rounded.Edit, "Cambiar foto", tint = Color.White, modifier = Modifier.size(18.dp))
                    }
                }
            }
        }
        Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 20.dp)) {
            Text(title, fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, color = c.text, maxLines = 2,
                overflow = TextOverflow.Ellipsis)
            if (owner.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(owner, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = c.text, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(4.dp))
            Text(info, color = c.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 16.dp, top = 4.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            actions()
            Spacer(Modifier.weight(1f))
            IconButton(onClick = onShuffle) { Icon(Icons.Rounded.Shuffle, "Aleatorio", tint = c.muted, modifier = Modifier.size(28.dp)) }
            Spacer(Modifier.width(8.dp))
            Box(Modifier.size(56.dp).clip(CircleShape).background(c.accent).combinedClickableCompat(onPlay),
                contentAlignment = Alignment.Center) {
                Icon(Icons.Rounded.PlayArrow, "Reproducir", tint = c.accentText, modifier = Modifier.size(34.dp))
            }
        }
    }
}

@Composable
fun PageHeader(title: String, subtitle: String? = null, actions: @Composable () -> Unit = {}) {
    val c = LocalBs.current
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
        Text(title, fontSize = 26.sp, fontWeight = FontWeight.ExtraBold, color = c.text, maxLines = 2,
            overflow = TextOverflow.Ellipsis)
        if (!subtitle.isNullOrBlank()) {
            Spacer(Modifier.height(2.dp))
            Text(subtitle, color = c.muted, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) { actions() }
    }
}

@Composable
fun PillButton(text: String, primary: Boolean = false, onClick: () -> Unit) {
    val c = LocalBs.current
    Box(
        Modifier
            .clip(RoundedCornerShape(18.dp))
            .background(if (primary) c.accent else c.hover)
            .combinedClickableCompat(onClick)
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Text(text, color = if (primary) c.accentText else c.text, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
    }
}

@OptIn(ExperimentalFoundationApi::class)
private fun Modifier.combinedClickableCompat(onClick: () -> Unit) = this.combinedClickable(onClick = onClick)

@Composable
fun TextInputDialog(
    title: String, initial: String = "", confirm: String = "Aceptar", onDismiss: () -> Unit,
    hint: String? = null, placeholder: String? = null, onConfirm: (String) -> Unit,
) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                if (hint != null) {
                    Text(hint, color = LocalBs.current.muted, fontSize = 13.sp)
                    Spacer(Modifier.height(12.dp))
                }
                OutlinedTextField(
                    value = text, onValueChange = { text = it }, singleLine = true,
                    placeholder = placeholder?.let { { Text(it) } },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                )
            }
        },
        confirmButton = { TextButton(onClick = { if (text.isNotBlank()) onConfirm(text.trim()) }) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
        containerColor = LocalBs.current.selected,
    )
}

@Composable
fun ConfirmDialog(title: String, text: String, confirm: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
        containerColor = LocalBs.current.selected,
    )
}
