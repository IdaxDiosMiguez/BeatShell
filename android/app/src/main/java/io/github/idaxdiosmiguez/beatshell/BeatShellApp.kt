package io.github.idaxdiosmiguez.beatshell

import android.app.Application
import io.github.idaxdiosmiguez.beatshell.core.Py

class BeatShellApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Python (yt-dlp y el núcleo de BeatShell) tarda un poco en arrancar: se hace ya, en segundo plano.
        Py.start(this)
    }
}
