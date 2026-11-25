package io.github.idaxdiosmiguez.beatshell.playback

import android.net.Uri
import android.util.Log
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.ResolvingDataSource
import io.github.idaxdiosmiguez.beatshell.core.Library
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.SPOTIFY
import io.github.idaxdiosmiguez.beatshell.core.StreamInfo
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.core.YOUTUBE
import java.io.IOException
import java.io.InterruptedIOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** Error al sacar el stream (no es de red): no tiene sentido que ExoPlayer lo reintente. */
class ResolveException(message: String) : IOException(message)

/** Sin conexión al sacar el stream: se reintenta y, si sigue, se para (no se salta la canción). */
class OfflineException(message: String) : IOException(message)

/**
 * Traduce las URI `beatshell://youtube/<id>` y `beatshell://spotify/<id>` al stream real de
 * YouTube, con sus cabeceras. Se llama desde los hilos de carga de ExoPlayer (puede bloquear).
 *
 * YouTube sirve el audio m4a de calidad unos 5 s después de pedirlo (antes responde 403). Para que
 * la canción que el usuario pulsa empiece al momento se usa el formato 18 (sin espera); las demás
 * de la cola se resuelven por adelantado en calidad alta y la espera no se nota.
 */
@UnstableApi
class StreamResolver(private val library: Library) : ResolvingDataSource.Resolver {
    /** uid de la entrada que suena ahora (la que el usuario está esperando). */
    @Volatile var currentUid: String? = null

    /** Llamado (en otro hilo) cuando una canción de Spotify se empareja con un vídeo. */
    var onMatched: ((uid: String, matched: Track) -> Unit)? = null

    // Stream usado por cada entrada de la cola: al buscar dentro de la canción hay que seguir
    // pidiendo el mismo archivo (los bytes de otro formato no casarían).
    private val pinned = ConcurrentHashMap<String, StreamInfo>()
    private val hq = ConcurrentHashMap<String, StreamInfo>()
    private val pending = ConcurrentHashMap<String, Future<*>>()
    private val pool = Executors.newFixedThreadPool(2) { r -> Thread(r, "stream-prefetch").apply { isDaemon = true } }

    override fun resolveDataSpec(dataSpec: DataSpec): DataSpec {
        val uri = dataSpec.uri
        if (uri.scheme != Track.SCHEME) return dataSpec
        val uid = uri.getQueryParameter("uid") ?: uri.toString()
        val stream = try {
            streamFor(uid, videoIdFor(uri, uid))
        } catch (e: InterruptedException) {
            throw InterruptedIOException("cancelado")
        } catch (e: IOException) {
            throw e
        } catch (e: io.github.idaxdiosmiguez.beatshell.core.BeatShellError) {
            throw if (e.network) OfflineException(e.message ?: "Sin conexión") else ResolveException(e.message ?: "No se pudo abrir la canción")
        } catch (e: Exception) {
            throw ResolveException(e.message ?: "No se pudo abrir la canción")
        }
        return dataSpec.buildUpon()
            .setUri(Uri.parse(stream.url))
            .setHttpRequestHeaders(dataSpec.httpRequestHeaders + stream.headers)
            .build()
    }

    private fun videoIdFor(uri: Uri, uid: String): String {
        if (uri.authority == YOUTUBE) return uri.lastPathSegment ?: throw ResolveException("Enlace no válido")
        if (uri.authority != SPOTIFY) throw ResolveException("Fuente desconocida")
        val track = Track.fromJson(uri.getQueryParameter("j") ?: throw ResolveException("Canción no válida"))
        return matchSpotify(track, uid)
    }

    private fun matchSpotify(track: Track, uid: String): String {
        library.match(track.key)?.videoId?.let { return it }
        val found = Py.matchBlocking(track)
        library.putMatch(track.key, found)
        onMatched?.invoke(uid, found)
        return found.videoId ?: throw ResolveException("No se encontró «${track.title}» en YouTube")
    }

    private fun valid(s: StreamInfo) = s.expireAt - 10 * 60_000 > System.currentTimeMillis()

    private fun streamFor(uid: String, vid: String): StreamInfo {
        pinned[uid]?.let { if (valid(it)) return waitAvailable(it) }
        val previous = pinned[uid]
        val stream = when {
            previous != null -> {
                // La URL caducó a mitad de canción: mismo formato, para que los bytes casen.
                val again = if (previous.fast) Py.streamBlocking(vid, true) else highQuality(vid)
                if (again.format != previous.format) throw ResolveException("El formato del audio cambió")
                again
            }
            uid == currentUid && library.fastStart -> {
                val ready = hq[vid]?.takeIf { valid(it) && it.availableAt <= System.currentTimeMillis() + 300 }
                ready ?: run {
                    prefetchVideo(vid) // para la próxima vez que suene
                    Py.streamBlocking(vid, true)
                }
            }
            else -> highQuality(vid)
        }
        pinned[uid] = stream
        val wait = (stream.availableAt - System.currentTimeMillis()).coerceAtLeast(0)
        Log.i(TAG, "$vid (${if (uid == currentUid) "actual" else "siguiente"}): ${stream.client} formato ${stream.format}" +
            if (wait > 0) ", espera $wait ms" else "")
        return waitAvailable(stream)
    }

    private fun highQuality(vid: String): StreamInfo {
        hq[vid]?.let { if (valid(it)) return it }
        pending[vid]?.let { runCatching { it.get() } }
        hq[vid]?.let { if (valid(it)) return it }
        return Py.streamBlocking(vid, false).also { hq[vid] = it }
    }

    private fun waitAvailable(s: StreamInfo): StreamInfo {
        val wait = s.availableAt - System.currentTimeMillis()
        if (wait > 0) Thread.sleep(wait + 250)
        return s
    }

    private fun prefetchVideo(vid: String) {
        if (hq[vid]?.let(::valid) == true || pending.containsKey(vid)) return
        pending[vid] = pool.submit {
            try {
                hq[vid] = Py.streamBlocking(vid, false)
                Log.i(TAG, "$vid: calidad alta preparada (${hq[vid]?.client} ${hq[vid]?.format})")
            } catch (e: Exception) {
                Log.w(TAG, "prefetch $vid: ${e.message}")
            } finally {
                pending.remove(vid)
            }
        }
    }

    /** Resuelve con antelación (emparejado de Spotify incluido) una canción que va a sonar. */
    fun prefetch(track: Track) {
        if (track.isLocal) return
        track.videoId?.let { prefetchVideo(it); return }
        if (track.spotifyUri == null) return
        library.match(track.key)?.videoId?.let { prefetchVideo(it); return }
        pool.submit {
            try {
                prefetchVideo(matchSpotify(track, track.uid))
            } catch (e: Exception) {
                Log.w(TAG, "prefetch ${track.title}: ${e.message}")
            }
        }
    }

    /** Tras un 403 a mitad de canción: olvida los streams de esa entrada. */
    fun invalidate(uid: String, track: Track?) {
        pinned.remove(uid)
        track?.videoId?.let { hq.remove(it) }
        track?.let { library.match(it.key) }?.videoId?.let { hq.remove(it) }
    }

    fun forget(uid: String) {
        pinned.remove(uid)
    }

    companion object {
        private const val TAG = "StreamResolver"
    }
}
