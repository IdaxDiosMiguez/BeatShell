package io.github.idaxdiosmiguez.beatshell.playback

import android.app.PendingIntent
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.net.toUri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.ResolvingDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.RenderersFactory
import androidx.media3.exoplayer.audio.MediaCodecAudioRenderer
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import io.github.idaxdiosmiguez.beatshell.R
import io.github.idaxdiosmiguez.beatshell.core.Library
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.Session
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.ui.MainActivity

/**
 * Servicio en primer plano (tipo mediaPlayback) que hace sonar la música. Sigue vivo con la
 * pantalla apagada y con la app cerrada mientras haya algo sonando; Media3 se encarga de la
 * notificación multimedia, la pantalla de bloqueo y los botones de auriculares/Bluetooth.
 */
@UnstableApi
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private lateinit var player: ExoPlayer
    private lateinit var resolver: StreamResolver
    private val main = Handler(Looper.getMainLooper())
    private var retriedUid: String? = null
    private var skipsInARow = 0
    private val saveRunnable = Runnable { saveSession() }

    // Aleatorio como en la versión de escritorio: se reordena la cola de verdad (lo que viene
    // después de la actual), así "Reproducir a continuación" sigue funcionando en aleatorio y
    // al desactivarlo se recupera el orden original. El shuffle propio de ExoPlayer no se usa.
    private var shuffle = false
    private var originalOrder: List<String>? = null

    override fun onCreate() {
        super.onCreate()
        Py.start(this)
        val library = Library.get(this)
        resolver = StreamResolver(library)
        resolver.onMatched = { uid, matched -> main.post { onSpotifyMatched(uid, matched) } }

        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(15_000)
            .setReadTimeoutMs(20_000)
        val dataSource = ResolvingDataSource.Factory(DefaultDataSource.Factory(this, http), resolver)
        val mediaSources = DefaultMediaSourceFactory(dataSource).setLoadErrorHandlingPolicy(NoRetryOnResolveErrors())
        // Solo audio: aunque el formato rápido de YouTube trae vídeo, no se decodifica.
        val renderers = RenderersFactory { handler, _, audioListener, _, _ ->
            arrayOf(MediaCodecAudioRenderer(this, MediaCodecSelector.DEFAULT, handler, audioListener))
        }
        player = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(mediaSources)
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true) // pausa al desconectar los auriculares
            .setWakeMode(C.WAKE_MODE_NETWORK) // CPU y Wi-Fi despiertos con la pantalla apagada
            .build()
        player.addListener(PlayerEvents())

        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_OPEN_PLAYER)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(open)
            .setCallback(SessionCallback())
            .build()
        setMediaNotificationProvider(
            DefaultMediaNotificationProvider.Builder(this).build().apply { setSmallIcon(R.drawable.ic_stat_beatshell) },
        )
        restoreSession()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    /** Al quitar la app de Recientes solo se para si no está sonando nada. */
    override fun onTaskRemoved(rootIntent: Intent?) {
        saveSession()
        val p = session?.player
        if (p == null || !p.playWhenReady || p.mediaItemCount == 0 || p.playbackState == Player.STATE_ENDED) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        saveSession()
        main.removeCallbacksAndMessages(null)
        session?.run {
            player.release()
            release()
        }
        session = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------ cola guardada
    private fun currentTracks(): List<Track> =
        (0 until player.mediaItemCount).mapNotNull { Track.fromMediaItem(player.getMediaItemAt(it)) }

    private fun saveSession() {
        if (!::player.isInitialized) return
        Session.save(this, currentTracks(), player.currentMediaItemIndex, player.currentPosition,
            shuffle, player.repeatMode)
    }

    private fun scheduleSave() {
        main.removeCallbacks(saveRunnable)
        main.postDelayed(saveRunnable, 1500)
    }

    private fun restoreSession() {
        val saved = Session.load(this) ?: return
        player.setMediaItems(saved.tracks.map { it.toMediaItem() }, saved.index, saved.positionMs)
        player.repeatMode = saved.repeat
        shuffle = saved.shuffle
        publishShuffle()
        resolver.currentUid = saved.tracks.getOrNull(saved.index)?.uid
        // No se prepara: no suena ni pide nada a la red hasta que el usuario pulse play.
    }

    // ------------------------------------------------------------------ eventos
    private fun prefetchUpcoming() {
        val tl = player.currentTimeline
        if (tl.isEmpty) return
        var i = player.currentMediaItemIndex
        val mode = if (player.repeatMode == Player.REPEAT_MODE_OFF) Player.REPEAT_MODE_OFF else Player.REPEAT_MODE_ALL
        repeat(2) {
            i = tl.getNextWindowIndex(i, mode, false)
            if (i == C.INDEX_UNSET) return
            Track.fromMediaItem(player.getMediaItemAt(i))?.let(resolver::prefetch)
        }
    }

    private fun items(from: Int = 0, to: Int = player.mediaItemCount) = (from until to).map { player.getMediaItemAt(it) }

    private fun publishShuffle() {
        session?.setSessionExtras(Bundle().apply { putBoolean(EXTRA_SHUFFLE, shuffle) })
    }

    private fun setShuffle(on: Boolean) {
        if (on == shuffle) return
        shuffle = on
        val count = player.mediaItemCount
        val cur = player.currentMediaItemIndex.coerceAtLeast(0)
        if (on) {
            originalOrder = items().map { it.mediaId }
            if (count - cur > 2) player.replaceMediaItems(cur + 1, count, items(cur + 1).shuffled())
        } else {
            val order = originalOrder.orEmpty().withIndex().associate { it.value to it.index }
            originalOrder = null
            if (order.isNotEmpty() && count > 1) {
                val all = items()
                val current = all.getOrNull(cur)
                // sortedBy es estable: lo añadido después de activar el aleatorio queda al final.
                val sorted = all.sortedBy { order[it.mediaId] ?: Int.MAX_VALUE }
                val k = sorted.indexOf(current).coerceAtLeast(0)
                // La actual no se toca (no se corta): se recoloca lo de antes y lo de después.
                player.replaceMediaItems(0, cur, sorted.subList(0, k))
                player.replaceMediaItems(k + 1, player.mediaItemCount, sorted.subList(k + 1, sorted.size))
            }
        }
        publishShuffle()
        prefetchUpcoming()
        scheduleSave()
    }

    private fun onSpotifyMatched(uid: String, matched: Track) {
        for (i in 0 until player.mediaItemCount) {
            val item = player.getMediaItemAt(i)
            if (item.mediaId != uid) continue
            if (item.mediaMetadata.artworkUri != null) return
            val art = matched.bigCover ?: return
            val updated = item.buildUpon()
                .setMediaMetadata(item.mediaMetadata.buildUpon().setArtworkUri(art.toUri()).build())
                .build()
            player.replaceMediaItem(i, updated) // misma URI: no interrumpe lo que suena
            return
        }
    }

    private inner class PlayerEvents : Player.Listener {
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            resolver.currentUid = mediaItem?.mediaId
            retriedUid = null
            prefetchUpcoming()
            scheduleSave()
        }

        override fun onTimelineChanged(timeline: Timeline, reason: Int) {
            if (reason == Player.TIMELINE_CHANGE_REASON_PLAYLIST_CHANGED) {
                prefetchUpcoming()
                scheduleSave()
            }
        }

        /** Si otro mando (Bluetooth, coche…) activa el aleatorio, se usa el nuestro. */
        override fun onShuffleModeEnabledChanged(shuffleModeEnabled: Boolean) {
            if (shuffleModeEnabled) {
                player.shuffleModeEnabled = false
                setShuffle(true)
            }
        }

        override fun onRepeatModeChanged(repeatMode: Int) = scheduleSave()

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            if (isPlaying) skipsInARow = 0 else scheduleSave()
        }

        override fun onPlayerError(error: PlaybackException) {
            val item = player.currentMediaItem ?: return
            val track = Track.fromMediaItem(item)
            val cause = generateSequence<Throwable>(error) { it.cause }.toList()
            Log.w(TAG, "Error reproduciendo ${track?.title}", error)
            val expired = cause.any { it is HttpDataSource.InvalidResponseCodeException && it.responseCode in setOf(403, 410) }
            val network = cause.any { it is HttpDataSource.HttpDataSourceException || it is OfflineException } && !expired
            when {
                // URL caducada o rechazada: se pide otra y se sigue desde el mismo punto (una vez).
                (expired || network) && retriedUid != item.mediaId -> {
                    retriedUid = item.mediaId
                    resolver.invalidate(item.mediaId, track)
                    player.prepare()
                    player.play()
                }
                // Esta canción no se puede reproducir: se pasa a la siguiente (pocas veces seguidas,
                // por si el problema no es de la canción).
                cause.any { it is ResolveException } && player.hasNextMediaItem() && skipsInARow < 3 -> {
                    skipsInARow++
                    resolver.forget(item.mediaId)
                    player.seekToNextMediaItem()
                    player.prepare()
                    player.play()
                }
            }
        }
    }

    // ------------------------------------------------------------------ sesión
    private inner class SessionCallback : MediaSession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val result = MediaSession.ConnectionResult.AcceptedResultBuilder(session, controller)
            // El aleatorio propio solo lo usa la interfaz de BeatShell.
            if (controller.packageName == packageName) {
                result.setAvailableSessionCommands(
                    MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                        .add(SessionCommand(CMD_SHUFFLE, Bundle.EMPTY)).build(),
                )
            }
            return result.build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            if (customCommand.customAction == CMD_SHUFFLE) {
                setShuffle(args.getBoolean(EXTRA_SHUFFLE))
                return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
            }
            return super.onCustomCommand(session, controller, customCommand, args)
        }

        /** Los MediaItem llegan del mando sin URI: se reconstruyen a partir de la pista. */
        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> =
            Futures.immediateFuture(mediaItems.map { Track.fromMediaItem(it)?.toMediaItem() ?: it }.toMutableList())

        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
            startIndex: Int,
            startPositionMs: Long,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            var items = mediaItems.map { Track.fromMediaItem(it)?.toMediaItem() ?: it }
            var index = if (startIndex == C.INDEX_UNSET) 0 else startIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0))
            if (shuffle && items.size > 1) {
                // Lista nueva en aleatorio: empieza por la elegida y el resto se baraja.
                originalOrder = items.map { it.mediaId }
                val first = items[index]
                items = listOf(first) + (items - first).shuffled()
                index = 0
            }
            resolver.currentUid = items.getOrNull(index)?.mediaId
            return Futures.immediateFuture(MediaSession.MediaItemsWithStartPosition(items, index, startPositionMs))
        }

        /** "Play" desde los auriculares o el Bluetooth con la app cerrada: retoma la última cola. */
        override fun onPlaybackResumption(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            isForPlayback: Boolean,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            val saved = Session.load(this@PlaybackService)
                ?: return Futures.immediateFailedFuture(UnsupportedOperationException("Nada que retomar"))
            resolver.currentUid = saved.tracks[saved.index].uid
            return Futures.immediateFuture(
                MediaSession.MediaItemsWithStartPosition(saved.tracks.map { it.toMediaItem() }, saved.index, saved.positionMs),
            )
        }
    }

    /** Los errores al sacar el stream no se arreglan reintentando: se informa al momento. */
    private class NoRetryOnResolveErrors : DefaultLoadErrorHandlingPolicy() {
        override fun getRetryDelayMsFor(loadErrorInfo: LoadErrorHandlingPolicy.LoadErrorInfo): Long {
            val chain = generateSequence<Throwable>(loadErrorInfo.exception) { it.cause }
            if (chain.any { it is ResolveException || (it is HttpDataSource.InvalidResponseCodeException && it.responseCode == 403) }) {
                return C.TIME_UNSET
            }
            return super.getRetryDelayMsFor(loadErrorInfo)
        }
    }

    companion object {
        private const val TAG = "PlaybackService"
        const val CMD_SHUFFLE = "beatshell.shuffle"
        const val EXTRA_SHUFFLE = "shuffle"
    }
}
