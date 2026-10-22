"""Иконки телефонной версии: та же «К», но фон на весь квадрат (iOS и Android сами скругляют углы).

Запуск: python phone/make_app_icon.py site/app   (нужен Pillow и шрифт SF с macOS)
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
out = Path(sys.argv[1]); S = 1024
def grad(w, h, top, bottom):
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1); g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((w, h))
hx = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
f = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 560); f.set_variation_by_name(b"Heavy")
def make(scale):
    img = grad(S, S, hx("#241a38"), hx("#120d1a")).convert("RGBA")
    letter = Image.new("L", (S, S), 0); d = ImageDraw.Draw(letter)
    fs = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", int(560 * scale)); fs.set_variation_by_name(b"Heavy")
    l, t, r, b = d.textbbox((0, 0), "К", font=fs)
    d.text(((S - (r - l)) / 2 - l, (S - (b - t)) / 2 - t + 8), "К", font=fs, fill=255)
    img.paste(grad(S, S, hx("#c4a8ff"), hx("#7c3aed")), (0, 0), letter)
    return img.convert("RGB")
full, safe = make(1.0), make(0.78)   # safe — для круглой маски Android
full.resize((180, 180), Image.LANCZOS).save(out / "apple-touch-icon.png")
full.resize((192, 192), Image.LANCZOS).save(out / "icon-192.png")
full.resize((512, 512), Image.LANCZOS).save(out / "icon-512.png")
safe.resize((512, 512), Image.LANCZOS).save(out / "icon-maskable.png")
