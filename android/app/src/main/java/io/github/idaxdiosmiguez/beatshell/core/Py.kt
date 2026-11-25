package io.github.idaxdiosmiguez.beatshell.core

import android.content.Context
import android.util.Log
import com.chaquo.python.PyException
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Objeto que Python llama durante las descargas (`progress.update(fraccion, texto)`). */
fun interface PyProgress {
    fun update(fraction: Float, label: String)
}

class BeatShellError(message: String, val network: Boolean = false) : Exception(message)

/** Stream directo de YouTube ya resuelto. */
data class StreamInfo(
    val url: String,
    val headers: Map<String, String>,
    /** Hora (ms) a la que caduca la URL. */
    val expireAt: Long,
    /** Hora (ms) desde la que YouTube sirve el audio (antes responde 403). */
    val availableAt: Long,
    val client: String,
    val format: String,
    val fast: Boolean,
)

/**
 * Acceso al núcleo Python (src/BeatShell) a través de Chaquopy. Python se arranca una vez;
 * todas las llamadas son bloqueantes y se hacen fuera del hilo principal.
 */
object Py {
    private const val TAG = "BeatShellPy"
    private val ready = CompletableDeferred<PyObject>()
    @Volatile var ytdlpVersion: String = "?"
        private set

    /** Arranca Python en segundo plano (desde Application.onCreate). */
    fun start(context: Context) {
        if (ready.isCompleted) return
        val app = context.applicationContext
        Thread({
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(app))
                val mod = Python.getInstance().getModule("beatshell_android")
                ytdlpVersion = mod.callAttr("init", app.filesDir.absolutePath).toString()
                ready.complete(mod)
            } catch (t: Throwable) {
                Log.e(TAG, "No se pudo arrancar Python", t)
                // Si el yt-dlp actualizado desde la app falla, se vuelve al que trae el APK.
                runCatching {
                    java.io.File(app.filesDir, "yt-dlp/yt_dlp.whl").delete()
                }
                ready.completeExceptionally(t)
            }
        }, "python-init").start()
    }

    private fun module(): PyObject = try {
        // Bloquea solo hasta que Python termina de arrancar (una vez).
        kotlinx.coroutines.runBlocking { ready.await() }
    } catch (t: Throwable) {
        throw BeatShellError("No se pudo iniciar el motor de YouTube: ${t.message}")
    }

    /** Llamada bloqueante. Traduce las excepciones de Python a un mensaje legible. */
    fun callBlocking(fn: String, vararg args: Any?): String {
        val mod = module()
        try {
            return mod.callAttr(fn, *args)?.toString() ?: ""
        } catch (e: PyException) {
            Log.w(TAG, "$fn falló: ${e.message?.lineSequence()?.firstOrNull()}")
            throw BeatShellError(cleanMessage(e.message), network = e.message.orEmpty().startsWith("ConnectionError"))
        }
    }

    suspend fun call(fn: String, vararg args: Any?): String = withContext(Dispatchers.IO) { callBlocking(fn, *args) }

    private fun cleanMessage(raw: String?): String {
        val first = (raw ?: "Error desconocido").lineSequence().first()
        // "YouTubeError: mensaje" → "mensaje"
        return first.replace(Regex("^[A-Za-z_.]*(Error|Exception): "), "").ifBlank { "Error desconocido" }
    }

    // ------------------------------------------------------------------ API
    suspend fun search(query: String): Collection = Collection.fromJson(call("search", query, 25))

    /** Texto escrito o compartido: búsqueda, vídeo, playlist de YouTube o enlace de Spotify. */
    suspend fun resolve(text: String): Collection = Collection.fromJson(call("resolve", text))

    fun extractLink(text: String): String? = runCatching { callBlocking("extract_link", text) }
        .getOrNull()?.takeIf { it.isNotBlank() && it != "None" }

    fun streamBlocking(videoId: String, fast: Boolean): StreamInfo {
        val o = JSONObject(callBlocking("stream", videoId, fast))
        val headers = buildMap {
            o.optJSONObject("headers")?.let { h -> h.keys().forEach { put(it, h.getString(it)) } }
        }
        return StreamInfo(
            url = o.getString("url"),
            headers = headers,
            expireAt = (o.optDouble("expire", 0.0) * 1000).toLong(),
            availableAt = (o.optDouble("available_at", 0.0) * 1000).toLong(),
            client = o.optString("client"),
            format = o.optString("format"),
            fast = o.optString("format") == "18",
        )
    }

    /** Busca en YouTube la mejor versión de una canción de Spotify. */
    fun matchBlocking(track: Track): Track = Track.fromJson(callBlocking("match", track.toJson().toString()))

    fun downloadBlocking(videoId: String, outBase: String, meta: JSONObject, progress: PyProgress): String =
        callBlocking("download", videoId, outBase, meta.toString(), progress)

    suspend fun updateYtdlp(): Pair<String, Boolean> {
        val o = JSONObject(call("update_ytdlp"))
        return o.getString("version") to o.getBoolean("updated")
    }

    suspend fun resetYtdlp() {
        call("reset_ytdlp")
    }
}
