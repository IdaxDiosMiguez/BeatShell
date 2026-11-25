package io.github.idaxdiosmiguez.beatshell.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.automirrored.rounded.QueueMusic
import androidx.compose.material.icons.rounded.Repeat
import androidx.compose.material.icons.rounded.RepeatOne
import androidx.compose.material.icons.rounded.Shuffle
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.SkipPrevious
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.media3.common.Player
import io.github.idaxdiosmiguez.beatshell.core.fmtTime

@Composable
fun MiniPlayer(ui: PlayerUi, player: PlayerController, onExpand: () -> Unit) {
    val c = LocalBs.current
    val t = ui.current ?: return
    val frac = if (ui.durationMs > 0) (ui.positionMs.toFloat() / ui.durationMs).coerceIn(0f, 1f) else 0f
    Column(Modifier.fillMaxWidth().background(c.side)) {
        Box(Modifier.fillMaxWidth().height(2.dp).background(c.border)) {
            Box(Modifier.fillMaxWidth(frac).height(2.dp).background(c.accent))
        }
        Row(
            Modifier.fillMaxWidth().clickable(onClick = onExpand).padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Cover(t.cover, Modifier.size(44.dp), radius = 6.dp)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(t.title, fontWeight = FontWeight.Bold, fontSize = 14.sp, color = c.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(t.displayArtist, color = c.muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            PlayPauseIcon(ui, size = 44, onClick = player::togglePlay, filled = false)
            IconButton(onClick = player::next) { Icon(Icons.Rounded.SkipNext, "Siguiente", tint = c.text) }
        }
    }
}

@Composable
private fun PlayPauseIcon(ui: PlayerUi, size: Int, onClick: () -> Unit, filled: Boolean) {
    val c = LocalBs.current
    Box(
        Modifier.size(size.dp).clip(CircleShape).background(if (filled) c.text else c.side).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        val tint = if (filled) c.bg else c.text
        if (ui.buffering) {
            CircularProgressIndicator(Modifier.size((size * 0.45).dp), color = tint, strokeWidth = 2.dp)
        } else {
            Icon(
                if (ui.isPlaying) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                contentDescription = if (ui.isPlaying) "Pausa" else "Reproducir",
                tint = tint, modifier = Modifier.size((size * 0.55).dp),
            )
        }
    }
}

/** Barra fina como la de escritorio: se toca o se arrastra para buscar. */
@Composable
private fun SeekBar(value: Float, onSeek: (Float, Boolean) -> Unit) {
    val c = LocalBs.current
    var width by remember { mutableFloatStateOf(1f) }
    var active by remember { mutableStateOf(false) }
    fun frac(x: Float) = (x / width).coerceIn(0f, 1f)
    Box(
        Modifier
            .fillMaxWidth()
            .height(28.dp)
            .onSizeChanged { width = it.width.toFloat().coerceAtLeast(1f) }
            .pointerInput(Unit) {
                detectTapGestures { onSeek(frac(it.x), true) }
            }
            .pointerInput(Unit) {
                detectHorizontalDragGestures(
                    onDragStart = { active = true; onSeek(frac(it.x), false) },
                    onDragEnd = { active = false; onSeek(Float.NaN, true) },
                    onDragCancel = { active = false; onSeek(Float.NaN, true) },
                ) { change, _ -> onSeek(frac(change.position.x), false) }
            },
        contentAlignment = Alignment.CenterStart,
    ) {
        Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(c.selected))
        Box(Modifier.fillMaxWidth(value).height(4.dp).clip(CircleShape).background(if (active) c.accent else c.text))
        Box(
            Modifier
                .padding(start = with(androidx.compose.ui.platform.LocalDensity.current) { (value * width).toDp() - 6.dp }.coerceAtLeast(0.dp))
                .size(12.dp).clip(CircleShape).background(c.text),
        )
    }
}

@Composable
fun FullPlayer(ui: PlayerUi, player: PlayerController, onCollapse: () -> Unit, onQueue: () -> Unit) {
    val c = LocalBs.current
    val t = ui.current
    Box(
        Modifier.fillMaxSize().background(
            Brush.verticalGradient(listOf(c.selected, c.bg, c.bg)),
        ).clickable(enabled = false) {},
    ) {
        Column(Modifier.fillMaxSize().systemBarsPadding().padding(horizontal = 24.dp)) {
            Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onCollapse) { Icon(Icons.Rounded.KeyboardArrowDown, "Cerrar", tint = c.text, modifier = Modifier.size(30.dp)) }
                Text(
                    if (ui.queue.isNotEmpty() && ui.currentIndex >= 0) "${ui.currentIndex + 1} de ${ui.queue.size}" else "",
                    color = c.muted, fontSize = 12.sp, modifier = Modifier.weight(1f),
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                )
                IconButton(onClick = onQueue) { Icon(Icons.AutoMirrored.Rounded.QueueMusic, "Cola", tint = c.text) }
            }
            BoxWithConstraints(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                val side = minOf(maxWidth, maxHeight, 420.dp)
                Cover(t?.bigCover, Modifier.size(side).aspectRatio(1f), radius = 14.dp, fallbackUrl = t?.cover)
            }
            Spacer(Modifier.height(20.dp))
            Text(t?.title ?: "Nada sonando", fontWeight = FontWeight.ExtraBold, fontSize = 22.sp, color = c.text,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(4.dp))
            Text(t?.displayArtist ?: "Busca algo para empezar", color = c.muted, fontSize = 15.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(14.dp))

            var dragging by remember { mutableStateOf(false) }
            var dragValue by remember { mutableFloatStateOf(0f) }
            val dur = ui.durationMs.coerceAtLeast(1)
            val value = if (dragging) dragValue else (ui.positionMs.toFloat() / dur).coerceIn(0f, 1f)
            SeekBar(value) { f, done ->
                val v = if (f.isNaN()) dragValue else f
                dragValue = v
                dragging = !done
                if (done) player.seekTo((v * dur).toLong())
            }
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth()) {
                Text(fmtTime((if (dragging) dragValue * dur else ui.positionMs.toFloat()) / 1000.0), color = c.faint, fontSize = 12.sp)
                Spacer(Modifier.weight(1f))
                Text(if (ui.durationMs > 0) fmtTime(ui.durationMs / 1000.0) else "--:--", color = c.faint, fontSize = 12.sp)
            }
            Spacer(Modifier.height(10.dp))
            Row(
                Modifier.fillMaxWidth().widthIn(max = 420.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = player::toggleShuffle) {
                    Icon(Icons.Rounded.Shuffle, "Aleatorio", tint = if (ui.shuffle) c.accent else c.muted)
                }
                IconButton(onClick = player::previous, modifier = Modifier.size(56.dp)) {
                    Icon(Icons.Rounded.SkipPrevious, "Anterior", tint = c.text, modifier = Modifier.size(36.dp))
                }
                PlayPauseIcon(ui, size = 72, onClick = player::togglePlay, filled = true)
                IconButton(onClick = player::next, modifier = Modifier.size(56.dp)) {
                    Icon(Icons.Rounded.SkipNext, "Siguiente", tint = c.text, modifier = Modifier.size(36.dp))
                }
                IconButton(onClick = player::cycleRepeat) {
                    Icon(
                        if (ui.repeat == Player.REPEAT_MODE_ONE) Icons.Rounded.RepeatOne else Icons.Rounded.Repeat,
                        when (ui.repeat) {
                            Player.REPEAT_MODE_ONE -> "Repetir una"
                            Player.REPEAT_MODE_ALL -> "Repetir todo"
                            else -> "Repetir"
                        },
                        tint = if (ui.repeat != Player.REPEAT_MODE_OFF) c.accent else c.muted,
                    )
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}
