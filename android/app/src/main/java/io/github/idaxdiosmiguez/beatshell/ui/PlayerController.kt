package io.github.idaxdiosmiguez.beatshell.ui

import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionToken
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.playback.OfflineException
import io.github.idaxdiosmiguez.beatshell.playback.PlaybackService
import io.github.idaxdiosmiguez.beatshell.playback.ResolveException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class PlayerUi(
    val connected: Boolean = false,
    val current: Track? = null,
    val currentIndex: Int = -1,
    val queue: List<Track> = emptyList(),
    val isPlaying: Boolean = false,
    val buffering: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val shuffle: Boolean = false,
    val repeat: Int = Player.REPEAT_MODE_OFF,
)

/** Mando a distancia del servicio de reproducción para la interfaz. */
class PlayerController(private val context: Context) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var controller: MediaController? = null
    private val _state = MutableStateFlow(PlayerUi())
    val state: StateFlow<PlayerUi> = _state.asStateFlow()
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val messages: SharedFlow<String> = _messages
    private val trackCache = HashMap<String, Track>()

    fun connect() {
        if (controller != null) return
        scope.launch {
            val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
            val c = runCatching { MediaController.Builder(context, token).setListener(SessionListener()).buildAsync().await() }
                .getOrNull() ?: return@launch
            controller = c
            c.addListener(object : Player.Listener {
                override fun onEvents(player: Player, events: Player.Events) {
                    if (events.contains(Player.EVENT_TIMELINE_CHANGED)) rebuildQueue()
                    refresh()
                }

                override fun onPlayerError(error: PlaybackException) {
                    _messages.tryEmit(describe(error))
                }
            })
            rebuildQueue()
            refresh()
            while (isActive) {
                if (c.isPlaying) refreshPosition()
                delay(500)
            }
        }
    }

    fun release() {
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
        controller?.release()
        controller = null
    }

    private inner class SessionListener : MediaController.Listener {
        override fun onExtrasChanged(controller: MediaController, extras: Bundle) = refresh()
    }

    private fun describe(e: PlaybackException): String {
        val chain = generateSequence<Throwable>(e) { it.cause }.toList()
        chain.firstOrNull { it is ResolveException || it is OfflineException }?.message?.let { return it }
        return when (e.errorCode) {
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "Sin conexión: no se pudo cargar la canción"
            PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS -> "YouTube rechazó el audio; prueba otra vez"
            else -> "No se pudo reproducir (${e.errorCodeName})"
        }
    }

    private fun trackOf(item: MediaItem): Track =
        trackCache.getOrPut(item.mediaId) {
            Track.fromMediaItem(item) ?: Track(title = item.mediaMetadata.title?.toString() ?: "Sin título",
                artist = item.mediaMetadata.artist?.toString() ?: "", uid = item.mediaId)
        }.let { t ->
            // La portada puede llegar después (canciones de Spotify emparejadas en YouTube).
            val art = item.mediaMetadata.artworkUri?.toString()
            if (t.thumb == null && art != null) t.copy(thumb = art).also { trackCache[item.mediaId] = it } else t
        }

    private fun rebuildQueue() {
        val c = controller ?: return
        val q = (0 until c.mediaItemCount).map { trackOf(c.getMediaItemAt(it)) }
        if (trackCache.size > q.size * 2 + 100) {
            val keep = q.map { it.uid }.toSet()
            trackCache.keys.retainAll(keep)
        }
        _state.value = _state.value.copy(queue = q)
    }

    private fun refresh() {
        val c = controller ?: return
        val idx = c.currentMediaItemIndex
        val q = _state.value.queue
        _state.value = _state.value.copy(
            connected = true,
            currentIndex = if (c.mediaItemCount == 0) -1 else idx,
            current = c.currentMediaItem?.let(::trackOf),
            isPlaying = c.isPlaying,
            buffering = c.playbackState == Player.STATE_BUFFERING && c.playWhenReady,
            positionMs = c.currentPosition.coerceAtLeast(0),
            durationMs = c.duration.takeIf { it > 0 } ?: ((q.getOrNull(idx)?.duration ?: 0.0) * 1000).toLong(),
            shuffle = c.sessionExtras.getBoolean(PlaybackService.EXTRA_SHUFFLE, false),
            repeat = c.repeatMode,
        )
    }

    private fun refreshPosition() {
        val c = controller ?: return
        _state.value = _state.value.copy(
            positionMs = c.currentPosition.coerceAtLeast(0),
            durationMs = c.duration.takeIf { it > 0 } ?: _state.value.durationMs,
        )
    }

    private fun withController(fn: (MediaController) -> Unit) {
        controller?.let(fn) ?: run {
            connect()
            scope.launch {
                repeat(40) {
                    controller?.let { fn(it); return@launch }
                    delay(100)
                }
            }
        }
    }

    // ------------------------------------------------------------------ órdenes
    fun playList(tracks: List<Track>, start: Int = 0) = withController { c ->
        if (tracks.isEmpty()) return@withController
        c.setMediaItems(tracks.map { it.copyNew().toMediaItem() }, start.coerceIn(0, tracks.lastIndex), 0)
        c.prepare()
        c.play()
    }

    /** Aleatorio sobre una lista: activa el modo aleatorio y empieza por una canción al azar. */
    fun shuffleList(tracks: List<Track>) = withController { c ->
        if (tracks.isEmpty()) return@withController
        setShuffle(true)
        playList(tracks, tracks.indices.random())
    }

    /** La pone justo después de la actual y salta a ella (la cola sigue igual). */
    fun playNow(track: Track) = withController { c ->
        if (c.mediaItemCount == 0) return@withController playList(listOf(track))
        val pos = c.currentMediaItemIndex + 1
        c.addMediaItem(pos, track.copyNew().toMediaItem())
        c.seekTo(pos, 0)
        c.prepare()
        c.play()
    }

    fun enqueue(tracks: List<Track>, next: Boolean) = withController { c ->
        if (tracks.isEmpty()) return@withController
        val items = tracks.map { it.copyNew().toMediaItem() }
        val idle = c.mediaItemCount == 0 || c.playbackState == Player.STATE_ENDED
        val pos = if (next && c.mediaItemCount > 0) c.currentMediaItemIndex + 1 else c.mediaItemCount
        c.addMediaItems(pos, items)
        if (idle) {
            c.seekTo(pos, 0)
            c.prepare()
            c.play()
        }
    }

    fun jump(index: Int) = withController { c ->
        c.seekTo(index, 0)
        c.prepare()
        c.play()
    }

    fun togglePlay() = withController { c ->
        if (c.isPlaying) c.pause() else {
            if (c.playbackState == Player.STATE_ENDED) c.seekTo(0, 0)
            c.prepare()
            c.play()
        }
    }

    fun next() = withController { it.seekToNextMediaItem() }

    fun previous() = withController { it.seekToPrevious() }

    fun seekTo(ms: Long) = withController { it.seekTo(ms); refreshPosition() }

    fun setShuffle(on: Boolean) = withController { c ->
        c.sendCustomCommand(SessionCommand(PlaybackService.CMD_SHUFFLE, Bundle.EMPTY),
            Bundle().apply { putBoolean(PlaybackService.EXTRA_SHUFFLE, on) })
    }

    fun toggleShuffle() = setShuffle(!_state.value.shuffle)

    fun cycleRepeat() = withController { c ->
        c.repeatMode = when (c.repeatMode) {
            Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
            Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
            else -> Player.REPEAT_MODE_OFF
        }
    }

    fun remove(index: Int) = withController { it.removeMediaItem(index) }

    fun move(from: Int, to: Int) = withController { c ->
        if (from in 0 until c.mediaItemCount && to in 0 until c.mediaItemCount) c.moveMediaItem(from, to)
    }

    /** Vacía la cola dejando la canción que suena. */
    fun clearQueue() = withController { c ->
        val idx = c.currentMediaItemIndex
        if (c.mediaItemCount == 0) return@withController
        if (idx + 1 < c.mediaItemCount) c.removeMediaItems(idx + 1, c.mediaItemCount)
        if (idx > 0) c.removeMediaItems(0, idx)
    }
}
