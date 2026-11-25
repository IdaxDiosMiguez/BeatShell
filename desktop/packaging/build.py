"""Construye el ejecutable de BeatShell para el sistema actual.

    python packaging/build.py            # app + archivo listo para compartir en dist/
    python packaging/build.py --deno     # además mete Deno dentro (YouTube sin instalar nada)

Resultado en desktop/dist/:
  macOS   → BeatShell.app  + BeatShell-<ver>-macos-<arch>.dmg
  Windows → BeatShell\\BeatShell.exe + BeatShell-<ver>-windows-x64.zip
  Linux   → BeatShell/BeatShell     + BeatShell-<ver>-linux-<arch>.tar.gz
"""

from __future__ import annotations

import argparse
import io
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent
DIST = DESKTOP / "dist"
VENDOR = HERE / "vendor" / "bin"


# La consola de Windows usa cp1252 y no sabe imprimir → ✔ ✖.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, str(DESKTOP))
from beatshell_desktop import __version__ as VERSION  # noqa: E402


def arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def os_name() -> str:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


DENO_TARGETS = {
    ("macos", "arm64"): "aarch64-apple-darwin",
    ("macos", "x64"): "x86_64-apple-darwin",
    ("windows", "x64"): "x86_64-pc-windows-msvc",
    ("linux", "x64"): "x86_64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
}


def fetch_deno() -> None:
    target = DENO_TARGETS.get((os_name(), arch()))
    if target is None:
        print("! Deno no disponible para esta plataforma; se usará el del sistema si existe")
        return
    url = f"https://github.com/denoland/deno/releases/latest/download/deno-{target}.zip"
    print(f"→ descargando Deno ({target})")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - URL fija
        data = resp.read()
    VENDOR.mkdir(parents=True, exist_ok=True)
    name = "deno.exe" if sys.platform == "win32" else "deno"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        (VENDOR / name).write_bytes(zf.read(name))
    (VENDOR / name).chmod((VENDOR / name).stat().st_mode | stat.S_IEXEC)


def run(cmd: list[str]) -> None:
    print("→", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=DESKTOP)


def package() -> Path:
    base = f"BeatShell-{VERSION}-{os_name()}-{arch()}"
    if sys.platform == "darwin":
        out = DIST / f"{base}.dmg"
        out.unlink(missing_ok=True)
        stage = DIST / "dmg"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        run(["ditto", str(DIST / "BeatShell.app"), str(stage / "BeatShell.app")])
        (stage / "Applications").symlink_to("/Applications")
        run(["hdiutil", "create", "-volname", "BeatShell", "-srcfolder", str(stage),
             "-ov", "-format", "UDZO", str(out)])
        shutil.rmtree(stage, ignore_errors=True)
        return out
    if sys.platform == "win32":
        out = DIST / f"{base}.zip"
        out.unlink(missing_ok=True)
        shutil.make_archive(str(out.with_suffix("")), "zip", DIST, "BeatShell")
        return out
    out = DIST / f"{base}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        tf.add(DIST / "BeatShell", arcname="BeatShell")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deno", action="store_true", help="incluir Deno dentro del ejecutable")
    ap.add_argument("--no-package", action="store_true", help="solo la app, sin .dmg/.zip/.tar.gz")
    args = ap.parse_args()

    if args.deno:
        fetch_deno()
    run([sys.executable, str(HERE / "make_icon.py")])
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(DESKTOP / "build"), str(HERE / "beatshell.spec")])
    if not args.no_package:
        print("✔", package())


if __name__ == "__main__":
    main()
