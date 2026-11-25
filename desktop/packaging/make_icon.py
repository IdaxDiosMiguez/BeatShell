"""Genera el icono de la app (PNG, ICO y ICNS) a partir del logo ▚ de BeatShell."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "beatshell_desktop" / "assets"
BG_TOP, BG_BOTTOM = (28, 22, 48), (12, 10, 20)
PINK, CYAN = (255, 79, 216), (94, 231, 255)


def draw(size: int = 1024) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # fondo: degradado vertical dentro de un cuadrado redondeado (margen estilo macOS)
    pad = int(s * 0.1)
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(s * 0.18), fill=255)
    img.paste(grad, (0, 0), mask)
    # ▚ en pixel art: dos bloques en diagonal
    d = ImageDraw.Draw(img)
    inner = s - 2 * pad
    cell = int(inner * 0.3)
    gap = int(inner * 0.015)
    cx = cy = s // 2
    r = int(cell * 0.12)
    d.rounded_rectangle([cx - gap // 2 - cell, cy - gap // 2 - cell, cx - gap // 2, cy - gap // 2],
                        radius=r, fill=PINK)
    d.rounded_rectangle([cx + gap // 2, cy + gap // 2, cx + gap // 2 + cell, cy + gap // 2 + cell],
                        radius=r, fill=CYAN)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(OUT / "icon.icns")
    print("iconos en", OUT)


if __name__ == "__main__":
    main()
