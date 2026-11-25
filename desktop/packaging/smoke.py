"""Prueba de humo del ejecutable ya construido (la usa CI).

Arranca la app sin pantalla, espera unos segundos y comprueba que llegó a cargarlo todo.
En CI no suele haber tarjeta de sonido: un error de audio se avisa, pero no falla la prueba.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"


def executable() -> Path:
    if sys.platform == "darwin":
        return DIST / "BeatShell.app" / "Contents" / "MacOS" / "BeatShell"
    return DIST / "BeatShell" / ("BeatShell.exe" if sys.platform == "win32" else "BeatShell")


def main() -> int:
    exe = executable()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "selftest.json"
        env = dict(os.environ, BEATSHELL_HOME=str(Path(tmp) / "home"), BEATSHELL_SELFTEST=str(out),
                   BEATSHELL_SELFTEST_SECONDS="10", QT_QPA_PLATFORM="offscreen")
        subprocess.run([str(exe)], env=env, timeout=90, check=False)
        if not out.exists():
            print("✖ la app no llegó a escribir el informe")
            return 1
        data = json.loads(out.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=1, ensure_ascii=False))
    if not data.get("booted"):
        print("✖ no terminó de arrancar")
        return 1
    if data.get("engine_error"):
        print("! sin audio en esta máquina (normal en CI):", data["engine_error"])
    print("✔ arranca bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
