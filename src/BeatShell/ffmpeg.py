"""Localiza un binario de ffmpeg: el del sistema si existe, si no el empaquetado por pip."""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys


@functools.lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "No se encontró ffmpeg. Instálalo (https://ffmpeg.org) o ejecuta "
            "`pip install imageio-ffmpeg`."
        ) from exc


# En Windows evitamos que cada proceso de ffmpeg abra una ventana de consola.
POPEN_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
