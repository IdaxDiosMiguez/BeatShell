# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: `python packaging/build.py` (no lo llames directamente).
#
# Modo carpeta (onedir) a propósito: arranca al instante. Un único .exe (onefile)
# tendría que descomprimirse entero cada vez que abres la app.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
DESKTOP = HERE.parent
ROOT = DESKTOP.parent
VENDOR_BIN = HERE / "vendor" / "bin"
ASSETS = DESKTOP / "beatshell_desktop" / "assets"
VERSION = "0.1.0"

datas = [(str(ASSETS), "beatshell_desktop/assets")]
datas += collect_data_files("imageio_ffmpeg")  # ffmpeg empaquetado (si no hay uno en el sistema)
datas += collect_data_files("certifi")

binaries = []
if VENDOR_BIN.is_dir():  # Deno, para que yt-dlp pueda leer YouTube sin instalar nada
    binaries += [(str(p), "bin") for p in VENDOR_BIN.iterdir() if p.is_file()]

hiddenimports = (
    collect_submodules("yt_dlp")
    + collect_submodules("BeatShell", filter=lambda m: not m.startswith("BeatShell.ui"))
    + ["mutagen", "sounddevice", "_sounddevice_data"]
)

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(DESKTOP), str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "textual", "textual_image", "BeatShell.ui", "matplotlib", "IPython",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia",
        "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.Qt3DCore",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

icon = str(ASSETS / ("icon.ico" if sys.platform == "win32" else "icon.icns"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BeatShell",
    console=False,
    icon=icon,
    upx=False,
    strip=False,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="BeatShell", upx=False, strip=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="BeatShell.app",
        icon=str(ASSETS / "icon.icns"),
        bundle_identifier="io.github.beatshell.desktop",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "BeatShell",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.music",
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,  # respeta el modo oscuro del sistema
        },
    )
