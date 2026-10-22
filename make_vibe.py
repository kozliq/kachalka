"""Рисует пиксельную гифку для длинных треков (дольше 2 минут) в режиме MP3:
полосатый кот в чёрных наушниках и с патчем-компасом на левом плече
кивает в такт в музыкальном магазине.
Морда кота берётся из фото и сжимается до пикселей-квадратиков с общей
палитрой на все кадры, наушники дорисованы поверх чёткими пикселями.
Кивок и покачивание — сдвиг картинки, наушники двигаются вместе с головой.

Запуск: python make_vibe.py [фото]  →  vibe.gif рядом со скриптом.
По умолчанию фото берётся из promo/vibe-src.png.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from make_cat import NOTES, draw_note

W, H = 240, 120     # размер "холста" в пикселях-квадратиках — мельче, чем у челика
SCALE = 2           # во сколько раз увеличить (480x240)
R = 4               # кадр сначала собирается в R раз крупнее, потом сжимается
N = 48              # кадров в цикле (25 к/с при 40 мс)
BEAT = 12           # кадров на удар — тот же темп, что у котика в автобусе
COLORS = 48         # общая палитра на все кадры, чтобы картинка не мерцала
TINT = (36, 26, 56)  # фиолетовый оттенок окна Качалки для фона
OUTLINE = (18, 14, 25)  # обводка кота — цвет фона сайта
SHOP_W = 72         # ширина полосы магазина без кота на фото (слева)
NOD = 3             # на сколько квадратиков опускается голова на удар
# кот на фото 476×418: контур головы с ушами
HEAD = [(80, 108), (128, 138), (160, 150), (200, 152), (250, 149), (300, 144), (335, 140),
        (365, 135), (394, 168), (398, 195), (405, 250), (412, 300), (424, 350), (436, 418),
        (203, 418), (173, 370), (148, 320), (133, 260), (118, 210), (106, 175), (94, 140)]
# наушники в тех же координатах: дужка — пол-эллипса, чашки — центры
BAND_C, BAND_R = (250, 160), (122, 82)
CUPS = [(130, 184), (366, 174)]
CUP_W, CUP_H = 14, 20           # чашка в квадратиках
HP = (24, 22, 30)               # чёрный пластик
HP_HI = (78, 72, 98)            # блик по краю
HP_SHINE = (96, 116, 176)       # синеватый отблеск, как на фото
# мини-патч в классике Стоника на левом плече кота (для зрителя — справа внизу):
# чёрный щиток со скруглённым верхом, две пуговицы, зелёный компас
PATCH_AT = (392, 366)
PATCH_PX = 1
PATCH = ["...kkkkkkk...",
         ".kkkkkkkkkkk.",
         "kbkkkkGkkkkbk",
         "kkkkkkGkkkkkk",
         "kkkgkkGkkgkkk",
         "kkkkgkGkgkkkk",
         "kkkkkgGgkkkkk",
         "kGGGGGyGGGGGk",
         "kkkkkgGgkkkkk",
         "kkkkgkGkgkkkk",
         "kkkgkkGkkgkkk",
         "kkkkkkGkkkkkk",
         "kkkkkkkkkkkkk",
         ".kkkkkkkkkkk."]
PATCH_COLORS = {"k": (14, 14, 16), "b": (78, 78, 86), "G": (58, 176, 86),
                "g": (30, 104, 52), "y": (214, 196, 60)}


def layers(src):
    """Магазин на весь кадр (из края фото, где кота нет) + вырезанный по контуру кот."""
    big_w, big_h = W * R, H * R
    # полоса магазина слева от кота, растянутая на всю ширину: ровные размытые полки
    strip = src.crop((0, 12, SHOP_W - 6, src.height))
    bg = strip.resize((big_w, big_h), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(R * 3))
    bg = Image.blend(ImageEnhance.Brightness(bg).enhance(0.7), Image.new("RGB", bg.size, TINT), 0.45)

    # голова кота по контуру, края чуть мягкие
    mask = Image.new("L", src.size, 0)
    ImageDraw.Draw(mask).polygon(HEAD, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    size = (round(src.width * big_h / src.height), big_h)
    fg = src.resize(size, Image.LANCZOS).filter(ImageFilter.UnsharpMask(R, 120, 2))
    return bg, fg, mask.resize(size, Image.LANCZOS)


def frame(bg, fg, mask, f):
    """Кивок вниз на каждый удар и лёгкое покачивание раз в два удара."""
    p = (f % BEAT) / BEAT
    nod = math.sin(math.pi * p) ** 2                       # 0 → 1 → 0 за удар
    dy = NOD * R * nod                                      # голова опускается
    sway = 0.04 * math.sin(2 * math.pi * f / (2 * BEAT))    # наклон влево-вправо
    h = fg.height
    # обратное преобразование: низ стоит, верх кивает и качается
    coeffs = (1, sway, -sway * h, 0, 1, -dy)
    moved = fg.transform(fg.size, Image.AFFINE, coeffs, Image.BICUBIC)
    m = mask.transform(mask.size, Image.AFFINE, coeffs, Image.BICUBIC, fillcolor=0)
    img = bg.copy()
    img.paste(moved, ((img.width - fg.width) // 2, 0), m)
    img = img.resize((W, H), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    # контур кота в квадратиках: по нему потом рисуется обводка
    full = Image.new("L", bg.size, 0)
    full.paste(m, ((img.width * R - fg.width) // 2, 0))
    cut = full.resize((W, H), Image.BOX).point(lambda v: 255 if v > 110 else 0)
    return ImageEnhance.Color(img).enhance(1.2), cut, (sway, NOD * nod)


def draw_headphones(d, at):
    """Дужка и две чашки чёткими пикселями; at переводит точку фото в квадратики кадра."""
    cx, cy = BAND_C
    rx, ry = BAND_R
    for i in range(121):
        a = math.pi + math.pi * i / 120
        x, y = at(cx + rx * math.cos(a), cy + ry * math.sin(a))
        d.rectangle([round(x), round(y), round(x) + 2, round(y) + 2], fill=HP)
        if 0.2 < i / 120 < 0.8:                                    # блик сверху дужки
            d.point((round(x) + 1, round(y)), fill=HP_HI)
    for n, (px, py) in enumerate(CUPS):
        x, y = at(px, py)
        x0, y0 = round(x - CUP_W / 2), round(y - CUP_H / 2)
        d.rounded_rectangle([x0, y0, x0 + CUP_W, y0 + CUP_H], radius=4, fill=HP)
        edge = x0 + 1 if n == 0 else x0 + CUP_W - 1                # наружный край
        d.line([(edge, y0 + 4), (edge, y0 + CUP_H - 4)], fill=HP_HI)
        if n == 1:
            d.line([(x0 + 4, y0 + 3), (x0 + 4, y0 + 7)], fill=HP_SHINE)


def draw_patch(d, at):
    """Патч с компасом пиксель в пиксель по рисунку PATCH, двигается вместе с котом."""
    x, y = at(*PATCH_AT)
    x0 = round(x) - len(PATCH[0]) * PATCH_PX // 2
    y0 = round(y) - len(PATCH) * PATCH_PX // 2
    for j, row in enumerate(PATCH):
        for i, ch in enumerate(row):
            if ch in PATCH_COLORS:
                px, py = x0 + i * PATCH_PX, y0 + j * PATCH_PX
                d.rectangle([px, py, px + PATCH_PX - 1, py + PATCH_PX - 1], fill=PATCH_COLORS[ch])


def make_frames(src_path):
    src = Image.open(src_path).convert("RGB")
    bg, fg, mask = layers(src)
    raw = [frame(bg, fg, mask, f) for f in range(N)]
    palette = raw[0][0].quantize(COLORS, method=Image.Quantize.MEDIANCUT)
    x0 = (W - fg.width / R) / 2
    k = H / src.height
    out = []
    for f, (img, cut, (sway, dy)) in enumerate(raw):
        def at(x, y):
            """Точка фото → квадратики кадра, с тем же кивком и наклоном, что у головы."""
            x, y = x0 + x * k, y * k
            return x + sway * (H - y), y + dy
        img = img.quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")
        # тёмная обводка в один квадратик, как у стикера
        ring = ImageChops.subtract(cut.filter(ImageFilter.MaxFilter(3)), cut)
        img.paste(OUTLINE, mask=ring)
        d = ImageDraw.Draw(img)
        draw_headphones(d, at)
        draw_patch(d, at)
        cups = [at(*c) for c in CUPS]
        for b in range(N // BEAT):
            t = ((f - b * BEAT) % N) / 30
            if t > 1:
                continue
            left = b % 2 == 0
            cx, cy = cups[0] if left else cups[1]
            x = cx + (-1 if left else 1) * (CUP_W / 2 + 22 * t) + 3 * math.sin(t * 7) - 4
            y = cy - 12 - 50 * t
            draw_note(d, round(x), round(y), NOTES[b % len(NOTES)], double=b % 2 == 1)
        out.append(img.resize((W * SCALE, H * SCALE), Image.NEAREST))
    return out


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "promo" / "vibe-src.png"
    out = here / "vibe.gif"
    frames = make_frames(src)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=40, loop=0, disposal=2, optimize=False)
    print(f"Готово: {out} ({len(frames)} кадров)")
