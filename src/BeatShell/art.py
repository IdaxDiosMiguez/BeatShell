"""Pixel art para terminal.

Cada celda de texto dibuja dos "píxeles" verticales con el carácter ▀ (color de
primer plano = píxel de arriba, fondo = píxel de abajo). Así una portada de
20×20 píxeles ocupa 20 columnas × 10 filas.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import os
import threading
from pathlib import Path

from rich.style import Style
from rich.text import Text

from .config import THUMB_DIR
from .models import Track

RGB = tuple[int, int, int]
Pixels = list[list[RGB | None]]  # None = transparente

UPPER = "▀"
LOWER = "▄"


def hexc(c: RGB) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def gradient(stops: list[RGB], t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    if len(stops) == 1:
        return stops[0]
    seg = t * (len(stops) - 1)
    i = min(int(seg), len(stops) - 2)
    return lerp(stops[i], stops[i + 1], seg - i)


def parse_hex(h: str) -> RGB:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def render(pixels: Pixels, bg: RGB | None = None) -> Text:
    """Convierte una matriz de píxeles en texto con medios bloques."""
    out = Text(no_wrap=True, overflow="crop")
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    cache: dict[tuple, Style] = {}
    for y in range(0, h, 2):
        top_row = pixels[y]
        bot_row = pixels[y + 1] if y + 1 < h else [None] * w
        for x in range(w):
            top = top_row[x] or bg
            bot = bot_row[x] or bg
            key = (top, bot)
            if top is None and bot is None:
                out.append(" ")
                continue
            st = cache.get(key)
            if top is not None and bot is not None:
                st = st or Style(color=hexc(top), bgcolor=hexc(bot))
                ch = UPPER
            elif top is not None:
                st = st or Style(color=hexc(top))
                ch = UPPER
            else:
                st = st or Style(color=hexc(bot))
                ch = LOWER
            cache[key] = st
            out.append(ch, st)
        if y + 2 < h:
            out.append("\n")
    return out


# ------------------------------------------------------------------ logo
_FONT = {
    "B": ["####.", "#...#", "####.", "#...#", "####."],
    "E": ["#####", "#....", "####.", "#....", "#####"],
    "A": [".###.", "#...#", "#####", "#...#", "#...#"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#.."],
    "S": [".####", "#....", ".###.", "....#", "####."],
    "H": ["#...#", "#...#", "#####", "#...#", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#####"],
}


def logo(stops: list[RGB], shadow: RGB, word: str = "BEATSHELL") -> Text:
    """Logo en fuente pixel 5×5 con degradado y sombra (3 filas de alto)."""
    gw = 5
    width = len(word) * (gw + 1) + 1
    height = 6
    px: Pixels = [[None] * width for _ in range(height)]
    for i, ch in enumerate(word):
        glyph = _FONT.get(ch)
        if not glyph:
            continue
        ox = i * (gw + 1)
        for y, row in enumerate(glyph):
            for x, cell in enumerate(row):
                if cell == "#":
                    if px[y + 1][ox + x + 1] is None:
                        px[y + 1][ox + x + 1] = shadow
        for y, row in enumerate(glyph):
            for x, cell in enumerate(row):
                if cell == "#":
                    t = (ox + x) / max(1, width - 2)
                    px[y][ox + x] = gradient(stops, t)
    return render(px)


# ------------------------------------------------------------------ portadas
def _seed(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


def sprite_cover(seed_text: str, size: int = 20) -> Pixels:
    """Portada generativa: un 'invader' simétrico único para cada canción."""
    size = max(8, size)
    d = _seed(seed_text or "beatshell")
    hue = d[0] / 255
    hue2 = (hue + 0.35 + d[1] / 255 * 0.3) % 1
    fg = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.65, 1.0))
    fg2 = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue2, 0.55, 0.95))
    bg1 = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.18))
    bg2 = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue2, 0.6, 0.10))
    grid_w, grid_h = 7, 7
    half = (grid_w + 1) // 2
    bits = int.from_bytes(d[2:10], "big")
    cells = [[False] * grid_w for _ in range(grid_h)]
    for y in range(grid_h):
        for x in range(half):
            on = bool(bits >> (y * half + x) & 1)
            cells[y][x] = cells[y][grid_w - 1 - x] = on
    # ojos garantizados para que siempre parezca una criatura
    cells[2][2] = cells[2][grid_w - 3] = False
    cells[1][2] = cells[1][grid_w - 3] = True
    scale = max(1, (size - 6) // grid_w)
    sw, sh = grid_w * scale, grid_h * scale
    ox, oy = (size - sw) // 2, (size - sh) // 2
    px: Pixels = []
    for y in range(size):
        row: list[RGB | None] = []
        for x in range(size):
            row.append(lerp(bg1, bg2, (x + y) / (2 * size)))
        px.append(row)
    # estrellas de fondo
    for i in range(6):
        sx, sy = d[12 + i] % size, d[20 + i] % size
        px[sy][sx] = lerp(px[sy][sx], (255, 255, 255), 0.35)  # type: ignore[arg-type]
    for gy in range(grid_h):
        for gx in range(grid_w):
            if cells[gy][gx]:
                col = fg if gy < grid_h // 2 + 1 else fg2
                for yy in range(scale):
                    for xx in range(scale):
                        py, pxx = oy + gy * scale + yy, ox + gx * scale + xx
                        if 0 <= py < size and 0 <= pxx < size:
                            px[py][pxx] = col  # type: ignore[assignment]
    # marco
    edge = lerp(fg, (0, 0, 0), 0.5)  # type: ignore[arg-type]
    for i in range(size):
        px[0][i] = px[size - 1][i] = px[i][0] = px[i][size - 1] = edge  # type: ignore[assignment]
    return px


def image_to_pixels(img, size: int, colors: int | None = 28) -> Pixels:
    from PIL import Image

    img = img.convert("RGB")
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    img = img.resize((size, size), Image.Resampling.BOX)
    if colors:
        # Paleta reducida → aspecto de pixel art auténtico.
        img = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
    data = list(img.getdata())
    return [[data[y * size + x] for x in range(size)] for y in range(size)]


# ------------------------------------------------------------------ carga
_FOLDER_ART = ("cover", "folder", "front", "album", "albumart")
_mem_cache: dict[str, bytes | None] = {}
_mem_lock = threading.Lock()


def _embedded_art(path: str) -> bytes | None:
    try:
        import mutagen

        f = mutagen.File(path)
        if f is None:
            return None
        tags = getattr(f, "tags", None)
        if tags is not None:
            if hasattr(tags, "getall"):
                pics = tags.getall("APIC")
                if pics:
                    front = [p for p in pics if getattr(p, "type", 0) == 3]
                    return (front or pics)[0].data
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                return bytes(covr[0])
            mbp = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
            if mbp:
                import base64

                from mutagen.flac import Picture

                return Picture(base64.b64decode(mbp[0])).data
        pics = getattr(f, "pictures", None)
        if pics:
            return pics[0].data
    except Exception:
        return None
    return None


def _folder_art(path: str) -> bytes | None:
    folder = Path(path).parent
    try:
        for entry in folder.iterdir():
            if entry.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and \
                    entry.stem.lower() in _FOLDER_ART:
                return entry.read_bytes()
    except OSError:
        pass
    return None


def cover_bytes(track: Track) -> bytes | None:
    """Bytes de la imagen de portada (bloqueante: llamar desde un hilo)."""
    key = track.key
    with _mem_lock:
        if key in _mem_cache:
            return _mem_cache[key]
    data: bytes | None = None
    disk = THUMB_DIR / f"{key.replace(':', '_')}.img"
    if track.path and os.path.exists(track.path):
        data = _embedded_art(track.path) or _folder_art(track.path)
    elif disk.exists():
        try:
            data = disk.read_bytes()
        except OSError:
            data = None
    if data is None and track.thumb:
        from .services import fetch_bytes

        data = fetch_bytes(track.thumb, timeout=10)
        if data:
            try:
                disk.write_bytes(data)
            except OSError:
                pass
    with _mem_lock:
        if data is not None or not track.thumb:
            _mem_cache[key] = data
    return data


def pixels_to_image(px: Pixels, scale: int = 16):
    """Matriz de píxeles → imagen PIL ampliada sin suavizar (píxeles nítidos)."""
    from PIL import Image

    h = len(px)
    w = len(px[0]) if h else 0
    img = Image.new("RGB", (w, h))
    img.putdata([c or (0, 0, 0) for row in px for c in row])
    return img.resize((w * scale, h * scale), Image.Resampling.NEAREST)


def cover_pixels(track: Track, size: int, pixel: bool = True) -> Pixels:
    data = cover_bytes(track)
    if data:
        try:
            from PIL import Image

            return image_to_pixels(Image.open(io.BytesIO(data)), size, 28 if pixel else None)
        except Exception:
            pass
    return sprite_cover(f"{track.artist}|{track.album or track.title}", size)
