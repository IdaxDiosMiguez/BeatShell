"""Punto de entrada: `beatshell` o `python -m BeatShell`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("BeatShell necesita Python 3.10 o superior.")
    if sys.platform == "win32":
        # Que los acentos y símbolos se vean bien en la consola de Windows.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="beatshell",
        description="Reproductor de música para terminal: YouTube, Spotify y archivos locales.",
        epilog='Ejemplos:  beatshell "daft punk"   ·   beatshell https://open.spotify.com/album/…'
               "   ·   beatshell ~/Música",
    )
    parser.add_argument("query", nargs="*",
                        help="búsqueda, enlace de YouTube/Spotify o ruta para empezar directamente")
    parser.add_argument("--version", action="version", version=f"BeatShell {__version__}")
    args = parser.parse_args()

    from .config import Config
    from .ui.widgets import Graphics

    Graphics.kitty = detect_kitty(Config.load().cover_renderer)

    from .ui.app import BeatShellApp

    BeatShellApp(initial=" ".join(args.query).strip() or None).run()


def detect_kitty(pref: str) -> bool:
    """¿Podemos dibujar portadas como imagen real (protocolo gráfico de Kitty)?

    Hay que preguntarlo a la terminal antes de que Textual tome el control.
    Kitty y Ghostty lo soportan; en el resto se usan bloques de colores.
    """
    if pref == "bloques" or not sys.stdout.isatty():
        return False
    try:
        import textual_image.renderable as renderable
        from textual_image.renderable.tgp import Image as TGPImage
        from textual_image.widget import TGPImage as _widget  # guarda el tamaño de celda en caché
    except Exception:
        return False
    del _widget
    return pref == "kitty" or renderable.Image is TGPImage


if __name__ == "__main__":
    main()
