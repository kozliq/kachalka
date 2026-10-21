"""Рисует пиксельную гифку для режима MP3: котик-фигурка в белых наушниках,
с бантиком и кристаллом из Sims над головой, едет в ночном автобусе
и качает головой в такт. Из наушников вылетают ноты.

Запуск: python make_cat.py  →  cat.gif рядом со скриптом.
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw

W, H = 160, 80      # размер "холста" в пикселях-квадратиках
SCALE = 3           # во сколько раз увеличить (480x240)
N = 48              # кадров в цикле (25 к/с при 40 мс)
BEAT = 12           # кадров на удар: 4 кивка за цикл ≈ 125 ударов в минуту
LAMP_GAP = 48       # фонари за окном идут через 48 пикселей…
LAMP_SPEED = 4      # …и летят по 4 пикселя за кадр — мимо окна ровно на каждый удар

# окно автобуса и сиденье
WIN = (108, 5, 155, 46)          # слева, сверху, справа, снизу
BACK = (8, 10, 102, 58)          # спинка сиденья
CUSHION_TOP, CUSHION_FRONT, FLOOR_Y = 56, 63, 70
CX = 54                          # где сидит котик

# палитра
BUS = "#241a38"
BUS_DARK = "#1a1229"
RAIL = "#8a5fb0"
WIN_FRAME = "#6b6378"
WIN_FRAME_DARK = "#4a4456"
SKY_TOP = "#1d1640"
SKY_LOW = "#33246a"
STAR = "#f0ebee"
MOON = "#f2e3b3"
CITY_FAR = "#261c4a"
CITY_NEAR = "#160f2a"
LIGHTS = ["#ffd166", "#ff8ac2", "#6ad7e8", "#ffd166"]
LAMP = "#f2e3b3"
LAMP_GLOW = "#6b5a8a"
SEAT = "#3b5aa8"
SEAT_WEAVE = "#3f5fb0"
SEAT_MOTIF = "#5374c4"
SEAT_EDGE = "#6a8ad6"
SEAT_SHADE = "#2b4485"
SEAT_DEEP = "#22386f"
FLOOR = "#2f2048"
FLOOR_LINE = "#3c2a5c"
FUR = "#e8c38c"
FUR_LIGHT = "#f7e0b5"
FUR_SHADE = "#cc9d63"
STRIPE = "#d3a46a"
EAR_IN = "#f2a6a6"
EYE = "#2a1a14"
NOSE = "#f07fa0"
MOUTH = "#7a4b35"
CHEEK = "#f5a8a0"
HP = "#f4f1f6"
HP_SHADE = "#cfc8d6"
HP_DARK = "#9a93a6"
BOW = "#ff7fb6"
BOW_DARK = "#d94f93"
BOW_HI = "#ffc2dd"
PLUMB_LIGHT = "#c6ff6b"
PLUMB = "#8ee03a"
PLUMB_DARK = "#5aa61f"
GLINT = "#f4ffe0"
NOTES = ["#ff8ac2", "#6ad7e8", "#ffd166", "#b388ff"]


def box(d, x, y, w, h, c):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=c)


# ---------- за окном ----------
def make_strip(width, seed, base_y, min_h, max_h, color, lit):
    """Полоса домов шириной width, повторяется по кругу без шва."""
    s = Image.new("RGBA", (width, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(s)
    rnd = seed
    x = 0
    while x < width:
        rnd = (rnd * 1103515245 + 12345) % 2 ** 31
        bw = min(6 + rnd % 8, width - x)
        bh = min_h + (rnd // 7) % (max_h - min_h + 1)
        top = base_y - bh
        box(d, x, top, bw, bh, color)
        if (rnd // 13) % 3 == 0 and bw > 8:          # антенна на крыше
            box(d, x + bw // 2, top - 3, 1, 3, color)
        if lit:                                       # горящие окна
            for wy in range(top + 2, base_y - 2, 3):
                for wx in range(x + 2, x + bw - 2, 3):
                    rnd = (rnd * 1103515245 + 12345) % 2 ** 31
                    if rnd % 4 == 0:
                        box(d, wx, wy, 1, 1, LIGHTS[(rnd // 5) % len(LIGHTS)])
        x += bw + (rnd // 11) % 2
    return s


def paste_loop(img, strip, offset):
    w = strip.width
    for bx in range(-(offset % w), W, w):
        img.paste(strip, (bx, 0), strip)


def lamp_positions(f):
    period = LAMP_GAP * 4
    return [(k * LAMP_GAP - f * LAMP_SPEED) % period - 24 for k in range(4)]


def draw_outside(img, f, far, near):
    d = ImageDraw.Draw(img)
    _, top, _, bottom = WIN
    for y in range(top, bottom):
        box(d, 0, y, W, 1, SKY_TOP if y < 24 else SKY_LOW)
    for sx, sy in [(114, 10), (128, 16), (147, 9), (139, 22), (120, 21)]:
        box(d, sx, sy, 1, 1, STAR)
    d.ellipse([141, 10, 147, 16], fill=MOON)
    d.ellipse([143, 8, 149, 14], fill=SKY_TOP)        # месяц
    # дальние дома ползут медленно, ближние быстрее, фонари быстрее всего
    paste_loop(img, far, f * 1)
    paste_loop(img, near, f * 2)
    for lx in lamp_positions(f):
        x = WIN[0] + lx
        box(d, x, 20, 1, bottom - 20, CITY_NEAR)
        box(d, x - 3, 19, 4, 1, CITY_NEAR)
        box(d, x - 4, 20, 3, 1, LAMP)
        box(d, x - 5, 21, 5, 1, LAMP_GLOW)


# ---------- салон ----------
def seat_texture():
    """Синяя ткань как в автобусе: мелкое плетение и ромбики."""
    t = Image.new("RGB", (W, H), SEAT)
    px = t.load()
    weave, motif = ImageColor.getrgb(SEAT_WEAVE), ImageColor.getrgb(SEAT_MOTIF)
    for y in range(H):
        for x in range(W):
            if (x + y) % 2 == 0:
                px[x, y] = weave
            u, v = (x + y) % 12, (x - y) % 12
            if (u == 0 and v < 6) or (v == 0 and u < 6):
                px[x, y] = motif
    return t


def seat_mask():
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle(BACK, radius=7, fill=255)
    d.rounded_rectangle([2, CUSHION_TOP, 112, FLOOR_Y], radius=3, fill=255)
    return m


def draw_bus(img, f, texture, mask):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = WIN
    # стенка вокруг окна
    box(d, 0, 0, W, y0, BUS)
    box(d, 0, y1, W, H - y1, BUS)
    box(d, 0, 0, x0, H, BUS)
    box(d, x1, 0, W - x1, H, BUS)
    box(d, 0, 0, W, 2, BUS_DARK)
    # серая рамка окна, как в настоящем автобусе
    d.rectangle([x0 - 2, y0 - 2, x1 + 1, y1 + 1], outline=WIN_FRAME, width=2)
    d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=WIN_FRAME_DARK, width=1)
    box(d, x0 - 2, y1 + 2, x1 - x0 + 4, 2, WIN_FRAME_DARK)  # подоконник
    # поручень с ручкой, которая качается от езды
    box(d, 0, 3, W, 1, RAIL)
    sway = round(1.5 * math.sin(2 * math.pi * f / N))
    hx = 132
    box(d, hx, 4, 1, 3, RAIL)
    d.rectangle([hx - 2 + sway, 7, hx + 2 + sway, 11], outline=RAIL)
    # сиденье: ткань по маске
    img.paste(texture, (0, 0), mask)
    d.line([BACK[0] + 6, BACK[1], BACK[2] - 6, BACK[1]], fill=SEAT_EDGE)       # кант сверху
    box(d, BACK[0] + 1, CUSHION_TOP - 2, BACK[2] - BACK[0] - 1, 2, SEAT_DEEP)  # складка
    box(d, 2, CUSHION_TOP, 111, 1, SEAT_EDGE)                                  # край подушки
    box(d, 2, CUSHION_FRONT, 111, FLOOR_Y - CUSHION_FRONT, SEAT_SHADE)         # перед подушки
    box(d, 2, FLOOR_Y - 1, 111, 1, SEAT_DEEP)
    # пол
    box(d, 0, FLOOR_Y + 1, W, H - FLOOR_Y - 1, FLOOR)
    for fx in range(0, W, 16):
        box(d, fx, 75, 8, 1, FLOOR_LINE)


def light_sweep(img, f, mask):
    """Свет фонарей проезжает по сиденью — видно, что автобус едет."""
    band = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(band)
    for lx in lamp_positions(f):
        x = WIN[0] + lx - 40         # свет падает в салон левее окна
        d.polygon([(x, 8), (x + 9, 8), (x + 1, 72), (x - 8, 72)], fill=34)
    band = ImageChops.multiply(band, mask)
    lit = Image.new("RGB", (W, H), "#c9b8ff")
    img.paste(lit, (0, 0), band)


# ---------- котик ----------
def ring(d, cx, cy, rx, ry, color):
    """Верхняя половина эллипса толщиной в пиксель — дуга наушников."""
    for i in range(361):
        a = math.pi + math.pi * i / 360
        box(d, round(cx + rx * math.cos(a)), round(cy + ry * math.sin(a)), 1, 1, color)


def draw_cat(d, f):
    beat = f // BEAT
    p = f % BEAT
    # кивок: резко вниз на ударе, плавно обратно; голову ведёт то вправо, то влево
    dy = [2, 3, 3, 2, 1, 1, 0, 0, 0, 0, 0, 1][p]
    dx = (1 if beat % 2 == 0 else -1) if p < 6 else 0
    squash = 1 if p in (1, 2) else 0
    cx = CX

    # тень на подушке
    d.ellipse([cx - 14, 59, cx + 13, 65], fill=SEAT_SHADE)

    # хвостик справа, кончик ходит туда-сюда
    sw = round(2 * math.sin(2 * math.pi * f / (BEAT * 2)))
    d.line([(cx + 7, 61), (cx + 12, 60), (cx + 14 + sw // 2, 57), (cx + 14 + sw, 54)],
           fill=FUR_SHADE, width=3)
    box(d, cx + 13 + sw, 53, 3, 2, FUR)

    # тело и задние лапы
    top = 45 + squash
    d.ellipse([cx - 11, 52, cx - 2, 63], fill=FUR_SHADE)
    d.ellipse([cx + 1, 52, cx + 10, 63], fill=FUR_SHADE)
    d.rounded_rectangle([cx - 8, top, cx + 7, 62], radius=6, fill=FUR)
    d.ellipse([cx - 4, top + 3, cx + 3, 60], fill=FUR_LIGHT)          # светлое пузико
    # передние лапки
    for px in (cx - 6, cx + 2):
        d.rounded_rectangle([px, 58, px + 4, 62], radius=2, fill=FUR_LIGHT)
        box(d, px + 1, 61, 1, 2, FUR_SHADE)
        box(d, px + 3, 61, 1, 2, FUR_SHADE)

    # голова: большая, как у фигурки
    hx, hy = cx - 11 + dx, 29 + dy
    # морда со щёчками и круглой макушкой
    d.rounded_rectangle([hx, hy, hx + 21, hy + 15], radius=6, fill=FUR)
    d.ellipse([hx + 2, hy - 3, hx + 19, hy + 9], fill=FUR)
    d.ellipse([hx - 1, hy + 6, hx + 9, hy + 16], fill=FUR)
    d.ellipse([hx + 12, hy + 6, hx + 22, hy + 16], fill=FUR)
    d.ellipse([hx + 6, hy + 9, hx + 15, hy + 16], fill=FUR_LIGHT)
    # дужка наушников лежит на макушке
    ring(d, hx + 10.5, hy + 7, 12, 11.5, HP)
    ring(d, hx + 10.5, hy + 7, 11, 10.5, HP)
    ring(d, hx + 10.5, hy + 7, 10, 9.5, HP_SHADE)
    # уши торчат перед дужкой
    d.polygon([(hx + 1, hy + 3), (hx + 4, hy - 5), (hx + 9, hy)], fill=FUR)
    d.polygon([(hx + 12, hy), (hx + 17, hy - 5), (hx + 20, hy + 3)], fill=FUR)
    d.polygon([(hx + 3, hy + 1), (hx + 4, hy - 2), (hx + 7, hy)], fill=EAR_IN)
    d.polygon([(hx + 14, hy), (hx + 17, hy - 2), (hx + 18, hy + 1)], fill=EAR_IN)
    box(d, hx + 3, hy + 16, 16, 1, FUR_SHADE)                        # тень под подбородком
    # полоски на лбу
    box(d, hx + 9, hy + 1, 1, 3, STRIPE)
    box(d, hx + 12, hy + 1, 1, 3, STRIPE)
    # большие тёмные глаза, раз за цикл моргает
    blink = f in (34, 35)
    for ex in (hx + 5, hx + 14):
        if blink:
            box(d, ex, hy + 9, 3, 1, EYE)
        else:
            box(d, ex, hy + 7, 3, 4, EYE)
            box(d, ex, hy + 7, 1, 1, "#ffffff")
            box(d, ex + 2, hy + 9, 1, 1, "#6b4a3a")
    # нос, ротик «w», румянец
    box(d, hx + 10, hy + 11, 2, 1, NOSE)
    for mx, my in ((hx + 9, hy + 12), (hx + 10, hy + 13), (hx + 11, hy + 13), (hx + 12, hy + 12)):
        box(d, mx, my, 1, 1, MOUTH)
    box(d, hx + 3, hy + 12, 2, 1, CHEEK)
    box(d, hx + 17, hy + 12, 2, 1, CHEEK)
    # чашки наушников
    for x in (hx - 5, hx + 19):
        d.ellipse([x, hy + 4, x + 7, hy + 15], fill=HP_DARK)
        d.ellipse([x + 1, hy + 5, x + 6, hy + 14], fill=HP)
        outer = x + 1 if x < hx else x + 5
        box(d, outer, hy + 7, 1, 5, HP_SHADE)
    # розовый бантик на правом ухе
    bx, by = hx + 16, hy - 2
    d.polygon([(bx - 4, by - 2), (bx, by), (bx - 4, by + 3)], fill=BOW)
    d.polygon([(bx + 4, by - 2), (bx, by), (bx + 4, by + 3)], fill=BOW)
    box(d, bx - 3, by - 1, 1, 1, BOW_HI)
    box(d, bx - 1, by - 1, 2, 3, BOW_DARK)
    return hx, hy, dy


def draw_plumbob(d, f, hx, hy, dy):
    """Зелёный кристалл из Sims: висит над головой, плавает и поворачивается."""
    cx = hx + 10
    top = hy - 24 - dy // 2 + round(1.5 * math.sin(2 * math.pi * f / N))
    turn = round(2 * math.sin(2 * math.pi * f / N))    # где проходит ребро
    for i, w in enumerate([1, 3, 5, 7, 9, 9, 7, 7, 5, 5, 3, 3, 1]):
        half = w // 2
        y = top + i
        for x in range(cx - half, cx + half + 1):
            edge = cx + max(-half, min(half, turn))
            c = PLUMB_LIGHT if x < edge else PLUMB if x == edge else PLUMB_DARK
            box(d, x, y, 1, 1, c)
    glint = (f // 6) % 8 == 0
    box(d, cx - 2, top + 3, 1, 1, GLINT if glint else PLUMB_LIGHT)


def draw_note(d, x, y, c, double):
    """Пиксельная нота ♪ или ♫."""
    box(d, x, y + 4, 3, 2, c)
    box(d, x + 2, y, 1, 5, c)
    if double:
        box(d, x + 6, y + 4, 3, 2, c)
        box(d, x + 8, y, 1, 5, c)
        box(d, x + 3, y, 5, 1, c)
    else:
        box(d, x + 3, y, 2, 1, c)
        box(d, x + 4, y + 1, 1, 1, c)


def make_frames():
    far = make_strip(96, 7, WIN[3], 8, 18, CITY_FAR, lit=False)
    near = make_strip(120, 3, WIN[3], 5, 14, CITY_NEAR, lit=True)
    texture = seat_texture()
    mask = seat_mask()
    frames = []
    for f in range(N):
        img = Image.new("RGB", (W, H), BUS)
        draw_outside(img, f, far, near)
        draw_bus(img, f, texture, mask)
        light_sweep(img, f, mask)
        d = ImageDraw.Draw(img)
        hx, hy, dy = draw_cat(d, f)
        draw_plumbob(d, f, hx, hy, dy)
        # на каждый удар из наушника вылетает нота: то слева, то справа
        for k in range(N // BEAT):
            t = ((f - k * BEAT) % N) / 30
            if t > 1:
                continue
            left = k % 2 == 0
            sx = hx - 8 if left else hx + 24
            x = sx + (-1 if left else 1) * 12 * t + 2 * math.sin(t * 7)
            y = hy + 2 - 24 * t
            draw_note(d, round(x), round(y), NOTES[k % len(NOTES)], double=k % 2 == 1)
        frames.append(img.resize((W * SCALE, H * SCALE), Image.NEAREST))
    return frames


if __name__ == "__main__":
    out = Path(__file__).with_name("cat.gif")
    frames = make_frames()
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=40, loop=0, disposal=2, optimize=False)
    print(f"Готово: {out} ({len(frames)} кадров)")
