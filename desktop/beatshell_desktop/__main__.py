"""Punto de entrada: `beatshell-desktop` o `python -m beatshell_desktop`."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _setup_env() -> None:
    """Rutas para encontrar ffmpeg y Deno aunque la app se abra desde el Finder/Explorador.

    Las apps de escritorio no heredan el PATH de la terminal (en macOS, Homebrew no está).
    En el ejecutable empaquetado, además, se usan los binarios que van dentro.
    """
    extra: list[str] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        extra.append(str(Path(bundle) / "bin"))
    home = Path.home()
    extra += [str(home / ".deno" / "bin")]
    if sys.platform == "darwin":
        extra += ["/opt/homebrew/bin", "/usr/local/bin"]
    elif sys.platform.startswith("linux"):
        extra += ["/usr/local/bin", "/usr/bin", str(home / ".local" / "bin")]
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            extra.append(str(Path(local) / "Microsoft" / "WinGet" / "Links"))
    current = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join([p for p in extra if p not in current and Path(p).is_dir()]
                                         + current)


def main() -> None:
    multiprocessing.freeze_support()  # el proceso de audio también arranca desde el ejecutable
    _setup_env()
    from .shell import run

    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
