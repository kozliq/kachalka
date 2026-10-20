"""Рисует иконку: фиолетовая «К» на тёмном фоне → icon.png / icon.ico / icon.icns."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 1024
FONTS = [
    ("/System/Library/Fonts/SFNS.ttf", b"Heavy"),
    ("/System/Library/Fonts/Supplemental/Arial Black.ttf", None),
    ("C:/Windows/Fonts/ariblk.ttf", None),
]


def font(size):
    for path, var in FONTS:
        if Path(path).exists():
            f = ImageFont.truetype(path, size)
            if var:
                try:
                    f.set_variation_by_name(var)
                except Exception:
                    pass
            return f
    return ImageFont.load_default(size)


def vertical_gradient(w, h, top, bottom):
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((w, h))


def hex2rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# плашка в пропорциях значков macOS: отступ 100, скругление 185
box = (100, 100, S - 100, S - 100)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(box, radius=185, fill=255)
bg = vertical_gradient(S, S, hex2rgb("#241a38"), hex2rgb("#120d1a"))
img.paste(bg, (0, 0), mask)
ImageDraw.Draw(img).rounded_rectangle(box, radius=185, outline=hex2rgb("#3a2f52"), width=6)

# буква «К» с фиолетовым переливом
f = font(600)
letter = Image.new("L", (S, S), 0)
d = ImageDraw.Draw(letter)
l, t, r, b = d.textbbox((0, 0), "К", font=f)
d.text(((S - (r - l)) / 2 - l, (S - (b - t)) / 2 - t + 10), "К", font=f, fill=255)
purple = vertical_gradient(S, S, hex2rgb("#c4a8ff"), hex2rgb("#7c3aed"))
img.paste(purple, (0, 0), letter)

img.save("icon.png")
img.save("icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save("icon.icns")
print("Иконка готова")
