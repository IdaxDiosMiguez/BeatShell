package io.github.idaxdiosmiguez.beatshell.download

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import io.github.idaxdiosmiguez.beatshell.R
import io.github.idaxdiosmiguez.beatshell.core.Library
import io.github.idaxdiosmiguez.beatshell.core.Py
import io.github.idaxdiosmiguez.beatshell.core.Track
import io.github.idaxdiosmiguez.beatshell.ui.MainActivity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

enum class DownloadState { QUEUED, RUNNING, DONE, FAILED }

data class DownloadJob(
    val id: String,
    val track: Track,
    val state: DownloadState = DownloadState.QUEUED,
    val progress: Float = 0f,
    val label: String = "En espera",
    val error: String? = null,
)

/** Lista de descargas compartida entre el servicio y la interfaz. */
object Downloads {
    private val _jobs = MutableStateFlow<List<DownloadJob>>(emptyList())
    val jobs: StateFlow<List<DownloadJob>> = _jobs.asStateFlow()

    fun enqueue(context: Context, tracks: List<Track>): Int {
        val busy = _jobs.value.filter { it.state != DownloadState.DONE }.map { it.track.key }.toSet()
        val new = tracks.filter { !it.isLocal && it.key !in busy }.distinctBy { it.key }
            .map { DownloadJob(Track.newUid(), it) }
        if (new.isEmpty()) return 0
        _jobs.update { it + new }
        ContextCompat.startForegroundService(context, Intent(context, DownloadService::class.java))
        return new.size
    }

    fun retryFailed(context: Context) {
        _jobs.update { l -> l.map { if (it.state == DownloadState.FAILED) it.copy(state = DownloadState.QUEUED, error = null, progress = 0f, label = "En espera") else it } }
        ContextCompat.startForegroundService(context, Intent(context, DownloadService::class.java))
    }

    fun clearFinished() = _jobs.update { l -> l.filter { it.state == DownloadState.QUEUED || it.state == DownloadState.RUNNING } }

    internal fun next(): DownloadJob? = _jobs.value.firstOrNull { it.state == DownloadState.QUEUED }

    internal fun set(id: String, fn: (DownloadJob) -> DownloadJob) = _jobs.update { l -> l.map { if (it.id == id) fn(it) else it } }
}

/**
 * Descarga canciones a M4A (el audio AAC original de YouTube, sin recodificar) y las guarda en
 * Música/BeatShell, donde aparecen también en "Música del teléfono". Corre en primer plano para
 * que no se corte al salir de la app.
 */
class DownloadService : LifecycleService() {
    private var worker: Job? = null
    private val nm by lazy { getSystemService(NotificationManager::class.java) }
    private var lastNotify = 0L

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        createChannel()
        ServiceCompat.startForeground(
            this, NOTIF_ID, notification("Preparando descargas…", null),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
        )
        if (worker?.isActive != true) {
            worker = lifecycleScope.launch {
                while (true) {
                    val job = Downloads.next() ?: break
                    run(job)
                }
                val done = Downloads.jobs.value.count { it.state == DownloadState.DONE }
                val failed = Downloads.jobs.value.count { it.state == DownloadState.FAILED }
                ServiceCompat.stopForeground(this@DownloadService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                if (done + failed > 0) {
                    val text = buildString {
                        append("$done descargada${if (done == 1) "" else "s"} en Música/BeatShell")
                        if (failed > 0) append(" · $failed con error")
                    }
                    nm.notify(NOTIF_DONE_ID, NotificationCompat.Builder(this@DownloadService, CHANNEL)
                        .setSmallIcon(R.drawable.ic_stat_beatshell).setContentTitle("Descargas terminadas")
                        .setContentText(text).setContentIntent(openApp()).setAutoCancel(true).build())
                }
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private suspend fun run(job: DownloadJob) {
        Downloads.set(job.id) { it.copy(state = DownloadState.RUNNING, label = "Buscando", progress = 0f) }
        val t = job.track
        try {
            withContext(Dispatchers.IO) {
                val library = Library.get(this@DownloadService)
                val vid = t.videoId ?: library.match(t.key)?.videoId ?: Py.matchBlocking(t).also {
                    library.putMatch(t.key, it)
                }.videoId ?: error("No se encontró en YouTube")
                val tmpDir = File(cacheDir, "downloads").apply { mkdirs() }
                val base = File(tmpDir, job.id).absolutePath
                val meta = JSONObject().apply {
                    put("title", t.title); put("artist", t.artist); put("album", t.album)
                    t.thumb?.takeIf { it.startsWith("http") && !it.contains("ytimg") }?.let { put("cover", it) }
                }
                val path = Py.downloadBlocking(vid, base, meta) { fraction, label ->
                    Downloads.set(job.id) { it.copy(progress = fraction, label = label) }
                    notifyProgress(t, fraction)
                }
                publish(File(path), t)
            }
            Downloads.set(job.id) { it.copy(state = DownloadState.DONE, progress = 1f, label = "Descargada") }
        } catch (e: Exception) {
            Downloads.set(job.id) { it.copy(state = DownloadState.FAILED, label = "Error", error = e.message) }
        }
    }

    /** Mueve el archivo a la carpeta pública Música/BeatShell. */
    private fun publish(file: File, t: Track) {
        val name = safeName(if (t.artist.isNotBlank()) "${t.artist} - ${t.title}" else t.title) + ".m4a"
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Audio.Media.DISPLAY_NAME, name)
                    put(MediaStore.Audio.Media.MIME_TYPE, "audio/mp4")
                    put(MediaStore.Audio.Media.RELATIVE_PATH, Environment.DIRECTORY_MUSIC + "/BeatShell")
                    put(MediaStore.Audio.Media.IS_PENDING, 1)
                }
                val coll = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                val uri = contentResolver.insert(coll, values) ?: error("No se pudo guardar el archivo")
                contentResolver.openOutputStream(uri)!!.use { out -> file.inputStream().use { it.copyTo(out) } }
                values.clear()
                values.put(MediaStore.Audio.Media.IS_PENDING, 0)
                contentResolver.update(uri, values, null, null)
            } else {
                // Android 9 o anterior: carpeta de música propia de la app (sin permisos extra).
                val dir = File(getExternalFilesDir(Environment.DIRECTORY_MUSIC), "BeatShell").apply { mkdirs() }
                val dest = File(dir, name)
                file.copyTo(dest, overwrite = true)
                MediaScannerConnection.scanFile(this, arrayOf(dest.absolutePath), arrayOf("audio/mp4"), null)
            }
        } finally {
            file.delete()
        }
    }

    private fun safeName(s: String) = s.replace(Regex("[\\\\/:*?\"<>|\\x00-\\x1f]"), "_").trim().take(120)

    private fun notifyProgress(t: Track, fraction: Float) {
        val now = System.currentTimeMillis()
        if (now - lastNotify < 700) return
        lastNotify = now
        val remaining = Downloads.jobs.value.count { it.state == DownloadState.QUEUED }
        nm.notify(NOTIF_ID, notification(t.title + if (remaining > 0) " · faltan $remaining" else "", fraction))
    }

    private fun openApp() = PendingIntent.getActivity(
        this, 1, Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_OPEN_DOWNLOADS),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun notification(text: String, fraction: Float?) = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_stat_beatshell)
        .setContentTitle("Descargando")
        .setContentText(text)
        .setOnlyAlertOnce(true)
        .setOngoing(true)
        .setContentIntent(openApp())
        .setProgress(100, ((fraction ?: 0f) * 100).toInt(), fraction == null)
        .build()

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Descargas", NotificationManager.IMPORTANCE_LOW))
    }

    companion object {
        private const val CHANNEL = "downloads"
        private const val NOTIF_ID = 2001
        private const val NOTIF_DONE_ID = 2002
    }
}
