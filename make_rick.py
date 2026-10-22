"""Рисует пиксельную гифку-рикролл: Рик Эстли танцует у микрофона.

Как в клипе Never Gonna Give You Up: рыжий кок, тёмно-синий пиджак,
тельняшка, светлые брюки, старый микрофон на стойке, сиреневый свет
из готического окна. Показывается вместо «Готово», когда Качалка
зарикроллила человека.

Запуск: python make_rick.py  →  rick.gif рядом со скриптом.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 160, 80      # размер "холста" в пикселях-квадратиках
SCALE = 3           # во сколько раз увеличить (480x240)
N = 16              # кадров в цикле: шаг влево + шаг вправо
DELAY = 70          # мс на кадр — примерно в темп песни (113 ударов в минуту)
GROUND = 71         # где стоят ноги
CX = 84             # где стоит Рик

# палитра
WALL = "#6a5aa8"
LIGHT = "#9b8cdb"
LIGHT_HI = "#c4b8f0"
LATTICE = "#57488f"
FLOOR = "#3f3274"
FLOOR_LINE = "#352a63"
FLOOR_SHINE = "#55479a"
SKIN = "#f2c29b"
SKIN_DARK = "#d9a07a"
HAIR = "#c0602b"
HAIR_HI = "#e08a4a"
HAIR_DARK = "#8a3f1c"
EYE = "#2a1d1a"
MOUTH = "#7a2f2a"
JACKET = "#27315e"
JACKET_DARK = "#1b2245"
JACKET_HI = "#3a4680"
STRIPE_W = "#f0ebee"
STRIPE_B = "#1a1424"
COLLAR = "#f0ebee"
PANTS = "#ece4d4"
PANTS_DARK = "#c9bfae"
BELT = "#1a1424"
SHOES = "#151318"
CHROME = "#d4cede"
CHROME_DARK = "#7d7590"
STAND = "#a39cb0"
NOTE = "#f0ebee"
NOTE_PINK = "#ff8ac2"


def box(d, x, y, w, h, c):
    if w > 0 and h > 0:
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=c)


def make_bg():
    """Сиреневая стена и свет из высокого готического окна, как в клипе."""
    bg = Image.new("RGB", (W, H), WALL)
    d = ImageDraw.Draw(bg)
    for wx in (22, 104):                      # два окна по бокам от Рика
        ww, top, bottom = 34, 8, 60
        # стрельчатый верх: каждая строка чуть уже
        for y in range(top, top + 12):
            k = (y - top) / 12
            half = round(ww / 2 * math.sqrt(1 - (1 - k) ** 2))
            box(d, wx + ww // 2 - half, y, half * 2, 1, LIGHT)
        box(d, wx, top + 12, ww, bottom - top - 12, LIGHT)
        # блики посередине стёкол
        for gx in range(wx + 2, wx + ww - 2, 8):
            for gy in range(top + 14, bottom - 2, 10):
                box(d, gx + 1, gy + 1, 3, 4, LIGHT_HI)
        # переплёт
        for gx in range(wx, wx + ww + 1, 8):
            box(d, gx, top + 4, 1, bottom - top - 4, LATTICE)
        for gy in range(top + 12, bottom + 1, 10):
            box(d, wx, gy, ww + 1, 1, LATTICE)
        box(d, wx + ww // 2, top, 1, 12, LATTICE)
    # пол сцены
    box(d, 0, GROUND, W, H - GROUND, FLOOR)
    box(d, 0, GROUND + 3, W, 1, FLOOR_LINE)
    for fx in range(0, W, 20):
        box(d, fx, GROUND + 4, 1, H - GROUND - 4, FLOOR_LINE)
    # отражение окон на полу
    for wx in (22, 104):
        box(d, wx + 4, GROUND + 1, 26, 1, FLOOR_SHINE)
    return bg


def limb(d, a, b, c, color, width):
    d.line([a, b], fill=color, width=width)
    d.line([b, c], fill=color, width=width)


def draw_rick(d, f):
    p = 2 * math.pi * f / N
    s = math.sin(p)                           # -1 … 1: шаг влево / вправо
    sway = round(2 * s)                       # верх тела качается
    dip = round(abs(math.sin(p)) * 1.4)       # на каждом шаге приседает
    cx = CX + sway
    top = GROUND - 60 + dip                   # макушка кока

    # ноги: стопы на месте, колени гуляют
    hip_y = GROUND - 21 + dip
    for side, foot_x in ((-1, CX - 5), (1, CX + 4)):
        lift = 1 if side * s < -0.3 else 0    # пятка противоположной ноги чуть отрывается
        hip = (cx + side * 3, hip_y)
        knee = (CX + side * 4 + round(1.5 * s), GROUND - 11 + dip // 2)
        foot = (foot_x, GROUND - 2 - lift)
        limb(d, hip, knee, foot, PANTS, 5)
        d.line([knee, foot], fill=PANTS_DARK, width=1)
        box(d, foot_x - 3, GROUND - 2 - lift, 7, 2, SHOES)

    # пиджак нараспашку, под ним тельняшка
    jy = top + 22
    box(d, cx - 9, jy + 1, 18, hip_y - jy + 1, JACKET)
    box(d, cx - 8, jy, 16, 1, JACKET)
    box(d, cx - 10, jy + 1, 1, 3, JACKET_DARK)  # подплечники, восьмидесятые же
    box(d, cx + 9, jy + 1, 1, 3, JACKET_DARK)
    box(d, cx - 9, jy + 4, 2, hip_y - jy - 2, JACKET_DARK)
    for i in range(12):                       # V-вырез: полоски через пиксель
        half = max(1, 4 - i // 3)
        box(d, cx - half, jy + i, half * 2, 1, STRIPE_W if i % 2 == 0 else STRIPE_B)
    box(d, cx - 1, jy + 12, 2, hip_y - jy - 10, STRIPE_B)
    box(d, cx - 2, hip_y - 1, 4, 1, BELT)
    for i in range(7):                        # лацканы
        box(d, cx - 5 + i // 2, jy + i, 1, 1, JACKET_HI)
        box(d, cx + 4 - i // 2, jy + i, 1, 1, JACKET_HI)
    box(d, cx - 6, jy + 12, 1, 1, JACKET_HI)  # пуговицы двубортного
    box(d, cx - 6, jy + 16, 1, 1, JACKET_HI)
    box(d, cx + 5, jy + 12, 1, 1, JACKET_HI)
    box(d, cx + 5, jy + 16, 1, 1, JACKET_HI)
    # белый воротник
    box(d, cx - 3, jy - 1, 6, 2, COLLAR)
    box(d, cx - 4, jy, 2, 2, COLLAR)
    box(d, cx + 2, jy, 2, 2, COLLAR)

    # руки: левая то к груди кулаком, то в сторону; правая качает кулаком
    sh_l, sh_r = (cx - 9, jy + 2), (cx + 8, jy + 2)
    a = (s + 1) / 2
    hand_l = (round(cx - 5 - 10 * a), round(jy + 9 - 6 * a))
    elbow_l = (cx - 12, jy + 9 - round(2 * a))
    b = 1 - a
    hand_r = (round(cx + 10 + 5 * b), round(jy + 4 + 9 * b))
    elbow_r = (cx + 12, jy + 9 + round(2 * b))
    for sh, el, hd in ((sh_l, elbow_l, hand_l), (sh_r, elbow_r, hand_r)):
        limb(d, sh, el, hd, JACKET_DARK, 5)   # контур, чтобы рука не сливалась с пиджаком
        limb(d, sh, el, hd, JACKET_HI, 3)
        box(d, hd[0] - 1, hd[1] - 1, 3, 3, COLLAR)  # манжета
        kx = hd[0] + (1 if hd[0] > el[0] else -1)   # кисть чуть дальше манжеты
        ky = hd[1] + (1 if hd[1] > el[1] else -1)
        box(d, kx - 1, ky - 1, 3, 3, SKIN)

    # голова
    hx, hy = cx - 5, top + 9
    box(d, hx, hy, 10, 12, SKIN)
    box(d, hx, hy + 9, 1, 3, SKIN_DARK)
    box(d, hx + 9, hy + 9, 1, 3, SKIN_DARK)
    box(d, hx - 1, hy + 5, 1, 3, SKIN_DARK)   # уши
    box(d, hx + 10, hy + 5, 1, 3, SKIN_DARK)
    box(d, hx + 2, hy + 4, 2, 1, HAIR_DARK)   # брови
    box(d, hx + 6, hy + 4, 2, 1, HAIR_DARK)
    box(d, hx + 2, hy + 5, 2, 1, EYE)
    box(d, hx + 6, hy + 5, 2, 1, EYE)
    box(d, hx + 5, hy + 6, 1, 2, SKIN_DARK)   # нос
    if f % 4 < 2:                             # поёт
        box(d, hx + 4, hy + 9, 3, 2, MOUTH)
    else:
        box(d, hx + 4, hy + 9, 3, 1, MOUTH)
    # тот самый рыжий кок: объём спереди, зачёсан вверх и назад
    box(d, hx + 1, hy - 6, 7, 1, HAIR)
    box(d, hx - 1, hy - 5, 11, 1, HAIR)
    box(d, hx - 2, hy - 4, 13, 4, HAIR)
    box(d, hx - 1, hy, 12, 2, HAIR)
    box(d, hx - 1, hy + 2, 2, 4, HAIR)        # бачки
    box(d, hx + 9, hy + 2, 2, 4, HAIR)
    box(d, hx + 2, hy - 5, 4, 1, HAIR_HI)     # блик на волне
    box(d, hx, hy - 4, 3, 1, HAIR_HI)
    box(d, hx + 7, hy - 3, 3, 1, HAIR_DARK)   # волна уходит назад
    box(d, hx - 2, hy - 1, 2, 1, HAIR_DARK)
    box(d, hx + 10, hy - 1, 1, 1, HAIR_DARK)
    box(d, hx + 1, hy + 2, 4, 1, HAIR)        # прядь на лоб
    box(d, hx + 2, hy + 2, 2, 1, HAIR_DARK)


def draw_mic(d):
    """Старый микрофон на стойке — чуть левее Рика, на уровне подбородка."""
    mx = CX - 11
    box(d, mx - 4, GROUND - 1, 9, 1, STAND)
    box(d, mx - 2, GROUND - 2, 5, 1, STAND)
    box(d, mx, GROUND - 40, 1, 39, STAND)
    y = GROUND - 49
    box(d, mx - 1, y, 3, 9, CHROME)
    for ry in range(y + 1, y + 9, 2):
        box(d, mx - 1, ry, 3, 1, CHROME_DARK)
    box(d, mx - 1, y + 9, 3, 1, STAND)


NOTES = [  # x, сдвиг по времени, цвет
    (16, 0, NOTE), (40, 5, NOTE_PINK), (122, 2, NOTE_PINK), (146, 9, NOTE), (100, 12, NOTE),
]


def draw_note(d, x, y, c):
    box(d, x, y + 4, 3, 2, c)                 # головка
    box(d, x + 2, y, 1, 5, c)                 # штиль
    box(d, x + 3, y, 2, 1, c)                 # флажок
    box(d, x + 4, y + 1, 1, 1, c)


def make_frames():
    bg = make_bg()
    frames = []
    for f in range(N):
        img = bg.copy()
        d = ImageDraw.Draw(img)
        for nx, shift, c in NOTES:            # ноты всплывают и качаются
            t = ((f + shift) % N) / N
            y = round(56 - 40 * t)
            x = nx + round(2 * math.sin(2 * math.pi * t))
            draw_note(d, x, y, c)
        draw_rick(d, f)
        draw_mic(d)
        frames.append(img.resize((W * SCALE, H * SCALE), Image.NEAREST))
    return frames


if __name__ == "__main__":
    out = Path(__file__).with_name("rick.gif")
    frames = make_frames()
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=DELAY, loop=0, disposal=2, optimize=False)
    print(f"Готово: {out} ({len(frames)} кадров)")
