package io.github.idaxdiosmiguez.beatshell.core

import android.net.Uri
import android.os.Bundle
import androidx.core.net.toUri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

const val LOCAL = "local"
const val YOUTUBE = "youtube"
const val SPOTIFY = "spotify"

/**
 * La misma pista que `BeatShell.models.Track` del núcleo (mismos campos y mismo JSON), así las
 * playlists se pueden pasar entre la versión de escritorio y la de Android.
 */
data class Track(
    val title: String,
    val artist: String = "",
    val album: String = "",
    val duration: Double? = null,
    val source: String = YOUTUBE,
    /** Archivo local: en Android, una URI content:// de MediaStore. */
    val path: String? = null,
    val url: String? = null,
    val spotifyUri: String? = null,
    val thumb: String? = null,
    val trackNo: Int? = null,
    val uid: String = newUid(),
) {
    val videoId: String? get() = youtubeId(url)
    val isLocal: Boolean get() = path != null
    val displayArtist: String get() = artist.ifBlank { "Artista desconocido" }

    /** Identidad de la canción (no de la entrada en la cola). */
    val key: String
        get() = when {
            path != null -> "f:$path"
            spotifyUri != null -> "s:" + spotifyUri.substringAfterLast(':')
            videoId != null -> "y:$videoId"
            else -> "t:$artist|$title"
        }

    /** Portada pequeña para listas. */
    val cover: String? get() = thumb ?: videoId?.let { "https://i.ytimg.com/vi/$it/mqdefault.jpg" }

    /** Portada grande (pantalla completa). Para YouTube, la de 1280 px si existe. */
    val bigCover: String?
        get() = videoId?.takeIf { thumb == null || thumb.contains("ytimg.com") }
            ?.let { "https://i.ytimg.com/vi/$it/maxresdefault.jpg" } ?: cover

    fun copyNew(): Track = copy(uid = newUid())

    fun toJson(): JSONObject = JSONObject().apply {
        put("title", title)
        if (artist.isNotEmpty()) put("artist", artist)
        if (album.isNotEmpty()) put("album", album)
        duration?.let { put("duration", it) }
        put("source", source)
        path?.let { put("path", it) }
        url?.let { put("url", it) }
        spotifyUri?.let { put("spotify_uri", it) }
        thumb?.let { put("thumb", it) }
        trackNo?.let { put("track_no", it) }
        put("uid", uid)
    }

    /** URI que entiende el reproductor; `StreamResolver` traduce las beatshell:// al stream. */
    fun playUri(): Uri = when {
        path != null -> path.toUri()
        videoId != null -> Uri.Builder().scheme(SCHEME).authority(YOUTUBE).appendPath(videoId)
            .appendQueryParameter("uid", uid).build()
        else -> Uri.Builder().scheme(SCHEME).authority(SPOTIFY)
            .appendPath(spotifyUri?.substringAfterLast(':') ?: "x")
            .appendQueryParameter("uid", uid)
            .appendQueryParameter("j", toJson().toString()).build()
    }

    fun toMediaItem(): MediaItem {
        val meta = MediaMetadata.Builder()
            .setTitle(title)
            .setArtist(displayArtist)
            .setAlbumTitle(album.ifEmpty { null })
            .setArtworkUri(bigCover?.toUri())
            .setDurationMs(duration?.let { (it * 1000).toLong() })
            .setIsPlayable(true)
            .setIsBrowsable(false)
            .setMediaType(MediaMetadata.MEDIA_TYPE_MUSIC)
            .setExtras(Bundle().apply { putString(EXTRA_TRACK, toJson().toString()) })
            .build()
        return MediaItem.Builder()
            .setMediaId(uid)
            .setUri(playUri())
            .setMediaMetadata(meta)
            .setRequestMetadata(MediaItem.RequestMetadata.Builder().setMediaUri(playUri()).build())
            .build()
    }

    companion object {
        const val SCHEME = "beatshell"
        const val EXTRA_TRACK = "beatshell.track"

        fun newUid(): String = UUID.randomUUID().toString().replace("-", "").take(12)

        fun fromJson(o: JSONObject): Track = Track(
            title = o.optString("title", "Sin título"),
            artist = o.optString("artist", ""),
            album = o.optString("album", ""),
            duration = if (o.has("duration") && !o.isNull("duration")) o.optDouble("duration") else null,
            source = o.optString("source", YOUTUBE),
            path = o.optStringOrNull("path"),
            url = o.optStringOrNull("url"),
            spotifyUri = o.optStringOrNull("spotify_uri"),
            thumb = o.optStringOrNull("thumb"),
            trackNo = if (o.has("track_no") && !o.isNull("track_no")) o.optInt("track_no") else null,
            uid = o.optStringOrNull("uid") ?: newUid(),
        )

        fun fromJson(s: String): Track = fromJson(JSONObject(s))

        fun listFromJson(a: JSONArray?): List<Track> =
            if (a == null) emptyList() else (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(::fromJson) }

        fun listToJson(tracks: List<Track>): JSONArray = JSONArray().apply { tracks.forEach { put(it.toJson()) } }

        fun fromMediaItem(item: MediaItem): Track? =
            item.mediaMetadata.extras?.getString(EXTRA_TRACK)?.let { runCatching { fromJson(it) }.getOrNull() }
    }
}

/** Una lista que viene de fuera o de la búsqueda: resultados, playlist, álbum o vídeo. */
data class Collection(
    val name: String,
    val tracks: List<Track>,
    val kind: String = "search",
    val source: String = YOUTUBE,
    val owner: String = "",
    val thumb: String? = null,
    val url: String? = null,
) {
    companion object {
        fun fromJson(s: String): Collection {
            val o = JSONObject(s)
            return Collection(
                name = o.optString("name"),
                tracks = Track.listFromJson(o.optJSONArray("tracks")),
                kind = o.optString("kind", "search"),
                source = o.optString("source", YOUTUBE),
                owner = o.optString("owner", ""),
                thumb = o.optStringOrNull("thumb"),
                url = o.optStringOrNull("url"),
            )
        }
    }
}

fun JSONObject.optStringOrNull(name: String): String? =
    if (has(name) && !isNull(name)) optString(name).takeIf { it.isNotEmpty() } else null

private val YT_ID = Regex("^[A-Za-z0-9_-]{11}$")

fun youtubeId(url: String?): String? {
    if (url.isNullOrBlank()) return null
    val u = runCatching { url.toUri() }.getOrNull() ?: return null
    val host = u.host?.lowercase() ?: return null
    val id = when {
        host.endsWith("youtu.be") -> u.pathSegments.firstOrNull()
        "youtube" in host -> u.getQueryParameter("v") ?: u.pathSegments.let { p ->
            if (p.size >= 2 && p[0] in setOf("shorts", "embed", "live", "v")) p[1] else null
        }
        else -> null
    }
    return id?.takeIf { YT_ID.matches(it) }
}

fun fmtTime(seconds: Double?): String {
    if (seconds == null || seconds.isNaN() || seconds < 0) return "--:--"
    val s = seconds.toLong()
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%d:%02d".format(m, sec)
}
