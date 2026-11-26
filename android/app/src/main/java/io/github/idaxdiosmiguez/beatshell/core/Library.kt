package io.github.idaxdiosmiguez.beatshell.core

import android.content.ContentUris
import android.content.Context
import android.provider.MediaStore
import androidx.core.content.edit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

const val KIND_PLAYLIST = "playlist"
const val KIND_ALBUM = "album"

/** Elemento guardado (mismo formato que `playlists.json` de la versión de escritorio). */
data class Playlist(
    val name: String,
    val tracks: List<Track> = emptyList(),
    val id: String = UUID.randomUUID().toString().replace("-", "").take(8),
    val created: Double = System.currentTimeMillis() / 1000.0,
    val sourceUrl: String? = null,
    val kind: String = KIND_PLAYLIST,
    val parent: String? = null,
    val artist: String = "",
    val thumb: String? = null,
    /** Foto elegida por ti (file://…); manda sobre [thumb] mientras exista en este teléfono. */
    val customThumb: String? = null,
) {
    val isFolder: Boolean get() = kind == "folder"
    val image: String? get() = customThumb?.takeIf { localExists(it) } ?: thumb
    val cover: String? get() = image ?: tracks.firstNotNullOfOrNull { it.cover }

    fun toJson(): JSONObject = JSONObject().apply {
        put("name", name); put("id", id); put("created", created)
        put("source_url", sourceUrl ?: JSONObject.NULL); put("kind", kind)
        put("parent", parent ?: JSONObject.NULL); put("artist", artist)
        put("thumb", thumb ?: JSONObject.NULL)
        put("custom_thumb", customThumb ?: JSONObject.NULL)
        put("tracks", Track.listToJson(tracks))
    }

    companion object {
        fun fromJson(o: JSONObject) = Playlist(
            name = o.getString("name"),
            id = o.getString("id"),
            created = o.optDouble("created", System.currentTimeMillis() / 1000.0),
            sourceUrl = o.optStringOrNull("source_url"),
            kind = o.optString("kind", KIND_PLAYLIST),
            parent = o.optStringOrNull("parent"),
            artist = o.optString("artist", ""),
            thumb = o.optStringOrNull("thumb"),
            customThumb = o.optStringOrNull("custom_thumb"),
            tracks = Track.listFromJson(o.optJSONArray("tracks")),
        )
    }
}

/** ¿Existe en este dispositivo la foto local `uri` (file://… o ruta)? */
internal fun localExists(uri: String): Boolean = File(uri.removePrefix("file://")).isFile

/** Escritura atómica: se escribe a un temporal y se renombra. */
internal fun File.writeAtomic(text: String) {
    parentFile?.mkdirs()
    val tmp = File(parentFile, "$name.tmp")
    tmp.writeText(text)
    if (!tmp.renameTo(this)) {
        delete(); tmp.renameTo(this)
    }
}

/** Playlists guardadas, emparejados de Spotify y ajustes. Un único objeto para toda la app. */
class Library private constructor(private val context: Context) {
    private val dir = context.filesDir
    private val playlistsFile = File(dir, "playlists.json")
    private val matchesFile = File(dir, "matches.json")
    private val prefs = context.getSharedPreferences("beatshell", Context.MODE_PRIVATE)
    private val lock = Any()

    private val _playlists = MutableStateFlow(loadPlaylists())
    val playlists: StateFlow<List<Playlist>> = _playlists.asStateFlow()

    private val matches: MutableMap<String, JSONObject> = loadMatches()

    // ------------------------------------------------------------------ ajustes
    /** Arranque al momento con el formato 18 (más datos) en vez de esperar ~5 s al m4a. */
    var fastStart: Boolean
        get() = prefs.getBoolean("fast_start", true)
        set(v) = prefs.edit { putBoolean("fast_start", v) }

    // ------------------------------------------------------------------ playlists
    private fun loadPlaylists(): List<Playlist> = runCatching {
        if (!playlistsFile.exists()) return emptyList()
        val a = JSONArray(playlistsFile.readText())
        (0 until a.length()).mapNotNull { i -> runCatching { Playlist.fromJson(a.getJSONObject(i)) }.getOrNull() }
    }.getOrDefault(emptyList())

    private fun save(list: List<Playlist>) {
        synchronized(lock) {
            _playlists.value = list
            playlistsFile.writeAtomic(JSONArray().apply { list.forEach { put(it.toJson()) } }.toString(1))
        }
    }

    private fun edit(fn: (List<Playlist>) -> List<Playlist>) = synchronized(lock) { save(fn(_playlists.value)) }

    fun playlist(id: String?): Playlist? = _playlists.value.firstOrNull { it.id == id }

    fun uniqueName(base: String): String {
        val names = _playlists.value.map { it.name.lowercase() }.toSet()
        if (base.lowercase() !in names) return base
        var i = 2
        while ("$base ($i)".lowercase() in names) i++
        return "$base ($i)"
    }

    fun create(
        name: String, tracks: List<Track> = emptyList(), kind: String = KIND_PLAYLIST,
        sourceUrl: String? = null, artist: String = "", thumb: String? = null,
    ): Playlist {
        val p = Playlist(uniqueName(name.trim().ifEmpty { "Nueva playlist" }), tracks.map { it.copyNew() },
            kind = kind, sourceUrl = sourceUrl, artist = artist, thumb = thumb)
        edit { it + p }
        return p
    }

    fun findSaved(col: Collection): Playlist? = _playlists.value.firstOrNull {
        (col.url != null && it.sourceUrl == col.url) || (it.name == col.name && it.kind == col.kind)
    }

    /**
     * Guarda playlists importadas. Si una ya estaba (mismo enlace) se actualizan sus canciones en
     * vez de duplicarla, y las descargadas siguen usando el archivo. Devuelve (nuevas, actualizadas).
     */
    fun importCollections(cols: List<Collection>): Pair<Int, Int> {
        var created = 0
        var updated = 0
        fun ident(t: Track) = t.spotifyUri ?: t.url ?: "${t.artist}|${t.title}".lowercase()
        edit { cur ->
            val out = cur.toMutableList()
            for (col in cols) {
                val kind = if (col.kind == "album") KIND_ALBUM else KIND_PLAYLIST
                val i = out.indexOfFirst { col.url != null && it.sourceUrl == col.url && it.kind == kind }
                if (i < 0) {
                    val names = out.map { it.name.lowercase() }.toSet()
                    var name = col.name.trim().ifEmpty { "Playlist" }
                    if (name.lowercase() in names) {
                        var n = 2
                        while ("$name ($n)".lowercase() in names) n++
                        name = "$name ($n)"
                    }
                    out += Playlist(name, col.tracks.map { it.copyNew() }, kind = kind, sourceUrl = col.url,
                        artist = col.owner, thumb = col.thumb)
                    created++
                } else {
                    val old = out[i].tracks.associateBy(::ident)
                    val tracks = col.tracks.map { t ->
                        val prev = old[ident(t)]
                        t.copyNew().copy(path = t.path ?: prev?.path, url = t.url ?: prev?.url)
                    }
                    out[i] = out[i].copy(tracks = tracks, thumb = col.thumb ?: out[i].thumb,
                        artist = col.owner.ifBlank { out[i].artist })
                    updated++
                }
            }
            out
        }
        return created to updated
    }

    // ------------------------------------------------------------------ fotos propias
    private val coversDir = File(dir, "covers")

    /** Copia la imagen elegida (recortada en cuadrado, 640 px, JPEG) y la pone como foto. */
    suspend fun saveCover(id: String, source: android.net.Uri) = withContext(Dispatchers.IO) {
        val bmp = decodeSquare(source) ?: throw BeatShellError("No se pudo abrir esa imagen")
        coversDir.mkdirs()
        val out = File(coversDir, "$id-${System.nanoTime()}.jpg")
        out.outputStream().use { bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, it) }
        bmp.recycle()
        setCustomThumb(id, "file://${out.absolutePath}")
    }

    fun setCustomThumb(id: String, uri: String?) {
        val old = playlist(id)?.customThumb
        edit { l -> l.map { if (it.id == id) it.copy(customThumb = uri) else it } }
        if (old != null && old != uri) {
            val f = File(old.removePrefix("file://"))
            if (f.parentFile?.canonicalPath == coversDir.canonicalPath) f.delete()
        }
    }

    private fun decodeSquare(uri: android.net.Uri, size: Int = 640): android.graphics.Bitmap? = runCatching {
        val src = if (android.os.Build.VERSION.SDK_INT >= 28) {
            // ImageDecoder respeta la orientación EXIF y reduce al decodificar (menos memoria).
            android.graphics.ImageDecoder.decodeBitmap(
                android.graphics.ImageDecoder.createSource(context.contentResolver, uri)) { dec, info, _ ->
                val side = minOf(info.size.width, info.size.height)
                if (side > size * 2) dec.setTargetSampleSize(side / (size * 2))
                dec.allocator = android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE
            }
        } else {
            val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
            context.contentResolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it, null, bounds) }
            val side = minOf(bounds.outWidth, bounds.outHeight).coerceAtLeast(1)
            val opts = android.graphics.BitmapFactory.Options().apply { inSampleSize = maxOf(1, side / (size * 2)) }
            context.contentResolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it, null, opts) }
        } ?: return null
        val side = minOf(src.width, src.height)
        val sq = android.graphics.Bitmap.createBitmap(src, (src.width - side) / 2, (src.height - side) / 2, side, side)
        val out = if (side > size) android.graphics.Bitmap.createScaledBitmap(sq, size, size, true) else sq
        if (sq !== src) src.recycle()
        if (out !== sq) sq.recycle()
        out
    }.getOrNull()

    fun rename(id: String, name: String) = edit { l -> l.map { if (it.id == id) it.copy(name = name.trim()) else it } }

    fun delete(id: String) = edit { l -> l.filterNot { it.id == id || it.parent == id } }

    fun addTo(id: String, tracks: List<Track>): Int {
        var added = 0
        edit { l ->
            l.map { p ->
                if (p.id != id) return@map p
                val keys = p.tracks.map { it.key }.toSet()
                val new = tracks.filter { it.key !in keys }.map { it.copyNew() }
                added = new.size
                p.copy(tracks = p.tracks + new)
            }
        }
        return added
    }

    fun removeFrom(id: String, uid: String) =
        edit { l -> l.map { p -> if (p.id == id) p.copy(tracks = p.tracks.filterNot { it.uid == uid }) else p } }

    fun moveIn(id: String, index: Int, delta: Int) = edit { l ->
        l.map { p ->
            if (p.id != id) return@map p
            val t = p.tracks.toMutableList()
            val j = (index + delta).coerceIn(0, t.lastIndex)
            if (index !in t.indices || j == index) return@map p
            t.add(j, t.removeAt(index))
            p.copy(tracks = t)
        }
    }

    /** Exporta todas las playlists en el formato de escritorio. */
    fun exportJson(): String = JSONArray().apply { _playlists.value.forEach { put(it.toJson()) } }.toString(1)

    /** Importa un `playlists.json` (de escritorio o de otro teléfono). Devuelve cuántas añadió. */
    fun importJson(text: String): Int {
        val a = runCatching { JSONArray(text) }.getOrElse { throw BeatShellError("El archivo no es un playlists.json válido") }
        val incoming = (0 until a.length()).mapNotNull { runCatching { Playlist.fromJson(a.getJSONObject(it)) }.getOrNull() }
        var n = 0
        edit { cur ->
            val ids = cur.map { it.id }.toSet()
            cur + incoming.filter { it.id !in ids && !it.isFolder }.map {
                n++
                // Rutas de archivos de otro equipo no sirven aquí: se quedan como YouTube/Spotify.
                it.copy(customThumb = it.customThumb?.takeIf { u -> localExists(u) },
                    tracks = it.tracks.map { t -> if (t.path?.startsWith("content://") == false) t.copy(path = null) else t }
                    .filter { t -> t.path != null || t.url != null || t.spotifyUri != null })
            }
        }
        return n
    }

    // ------------------------------------------------------------------ Spotify → YouTube
    private fun loadMatches(): MutableMap<String, JSONObject> = runCatching {
        val o = JSONObject(matchesFile.readText())
        o.keys().asSequence().associateWith { o.getJSONObject(it) }.toMutableMap()
    }.getOrDefault(mutableMapOf())

    fun match(key: String): Track? = synchronized(matches) { matches[key] }?.let { Track.fromJson(it) }

    fun putMatch(key: String, track: Track) {
        synchronized(matches) {
            matches[key] = track.toJson()
            matchesFile.writeAtomic(JSONObject(matches as Map<*, *>).toString())
        }
    }

    // ------------------------------------------------------------------ música del teléfono
    suspend fun localTracks(): List<Track> = withContext(Dispatchers.IO) {
        val uri = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        val cols = arrayOf(
            MediaStore.Audio.Media._ID, MediaStore.Audio.Media.TITLE, MediaStore.Audio.Media.ARTIST,
            MediaStore.Audio.Media.ALBUM, MediaStore.Audio.Media.DURATION, MediaStore.Audio.Media.ALBUM_ID,
            MediaStore.Audio.Media.TRACK,
        )
        val sel = "${MediaStore.Audio.Media.IS_MUSIC} != 0"
        val out = mutableListOf<Track>()
        context.contentResolver.query(uri, cols, sel, null, "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE")?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val artist = c.getString(2)?.takeUnless { it == MediaStore.UNKNOWN_STRING } ?: ""
                val albumId = c.getLong(5)
                out += Track(
                    title = c.getString(1) ?: "Sin título",
                    artist = artist,
                    album = c.getString(3) ?: "",
                    duration = c.getLong(4).takeIf { it > 0 }?.let { it / 1000.0 },
                    source = LOCAL,
                    path = ContentUris.withAppendedId(uri, id).toString(),
                    thumb = "content://media/external/audio/albumart/$albumId",
                    trackNo = c.getInt(6).takeIf { it > 0 }?.rem(1000),
                    uid = "local$id",
                )
            }
        }
        out
    }

    companion object {
        @Volatile private var instance: Library? = null
        fun get(context: Context): Library =
            instance ?: synchronized(this) { instance ?: Library(context.applicationContext).also { instance = it } }
    }
}

/** Cola guardada para restaurarla al volver a abrir la app (o al pulsar play en los auriculares). */
object Session {
    private fun file(context: Context) = File(context.filesDir, "session.json")

    fun save(context: Context, tracks: List<Track>, index: Int, positionMs: Long, shuffle: Boolean, repeat: Int) {
        val o = JSONObject().apply {
            put("index", index); put("position_ms", positionMs)
            put("shuffle", shuffle); put("repeat", repeat)
            put("tracks", Track.listToJson(tracks))
        }
        runCatching { file(context).writeAtomic(o.toString()) }
    }

    data class Saved(val tracks: List<Track>, val index: Int, val positionMs: Long, val shuffle: Boolean, val repeat: Int)

    fun load(context: Context): Saved? = runCatching {
        val o = JSONObject(file(context).readText())
        val tracks = Track.listFromJson(o.optJSONArray("tracks"))
        if (tracks.isEmpty()) return null
        Saved(tracks, o.optInt("index").coerceIn(0, tracks.lastIndex), o.optLong("position_ms"),
            o.optBoolean("shuffle"), o.optInt("repeat"))
    }.getOrNull()
}
