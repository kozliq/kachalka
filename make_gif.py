"""Рисует пиксельную гифку: челик бежит по офису с папкой, в неё летят файлы.

Запуск: python make_gif.py  →  animation.gif рядом со скриптом.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 160, 80      # размер "холста" в пикселях-квадратиках
SCALE = 3           # во сколько раз увеличить (480x240)
N = 48              # кадров в цикле (25 к/с при 40 мс)
TILE = 96           # ширина повторяющегося куска офиса
SPEED = TILE // N   # сдвиг фона за кадр
GROUND = 70         # где стоят ноги

# палитра
WALL = "#241a38"
WALL2 = "#2d2145"
BASE = "#1a1229"
FLOOR = "#3c2a5c"
FLOOR_LINE = "#2f2048"
FRAME = "#6b4f9a"
SKY = "#2a1f5c"
STAR = "#f0ebee"
MOON = "#f2e3b3"
WOOD = "#8a5fb0"
WOOD_DARK = "#5b3f80"
BINDERS = ["#e86ad0", "#b388ff", "#6ad7e8", "#ff8ac2", "#7c6cf0"]
PLANT = "#5cd68a"
POT = "#c0703f"
SKIN = "#f2c29b"
HAIR = "#3a2a22"
SHIRT = "#c77dff"
SHIRT_DARK = "#9a55d9"
PANTS = "#5b6bd6"
SHOES = "#151318"
FOLDER = "#ffd166"
FOLDER_DARK = "#d9a93e"
PAPER = "#f0ebee"
PAPER_LINE = "#9a93a0"
SPEEDLINE = "#4a3a6b"


def box(d, x, y, w, h, c):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=c)


def make_tile():
    """Кусок офиса шириной TILE: окно, полка с папками, цветок."""
    t = Image.new("RGB", (TILE, H), WALL)
    d = ImageDraw.Draw(t)
    box(d, 0, 40, TILE, 2, WALL2)
    # окно с ночным небом
    box(d, 6, 8, 30, 26, FRAME)
    box(d, 8, 10, 26, 22, SKY)
    box(d, 20, 10, 2, 22, FRAME)
    box(d, 8, 20, 26, 2, FRAME)
    for sx, sy in [(11, 13), (27, 15), (14, 26), (31, 27)]:
        box(d, sx, sy, 1, 1, STAR)
    box(d, 25, 12, 3, 3, MOON)
    # полка с папками
    box(d, 46, 14, 40, 2, WOOD)
    box(d, 46, 30, 40, 2, WOOD)
    box(d, 46, 46, 40, 2, WOOD)
    box(d, 46, 14, 2, 36, WOOD_DARK)
    box(d, 84, 14, 2, 36, WOOD_DARK)
    x = 49
    for i, hgt in enumerate([12, 10, 13, 11, 12, 9, 13]):
        c = BINDERS[i % len(BINDERS)]
        box(d, x, 30 - hgt, 4, hgt, c)
        box(d, x + 1, 30 - hgt + 3, 2, 2, PAPER)
        x += 5
    for i, (bx, bw) in enumerate([(49, 10), (61, 8), (71, 12)]):
        box(d, bx, 38, bw, 8, BINDERS[(i + 2) % len(BINDERS)])
        box(d, bx + bw // 2 - 1, 41, 3, 2, PAPER)
    # цветок в горшке
    box(d, 89, 50, 6, 6, POT)
    for lx, ly in [(90, 44), (92, 41), (88, 46), (93, 45), (91, 47)]:
        box(d, lx, ly, 2, 4, PLANT)
    # плинтус и пол
    box(d, 0, 56, TILE, 3, BASE)
    box(d, 0, 59, TILE, H - 59, FLOOR)
    box(d, 0, 66, TILE, 1, FLOOR_LINE)
    for fx in range(0, TILE, 24):
        box(d, fx, 59, 1, 7, FLOOR_LINE)
        box(d, fx + 12, 67, 1, H - 67, FLOOR_LINE)
    return t


def leg(d, hip, phase_angle, color):
    """Нога: бедро → колено → стопа, двигается по синусу."""
    hx, hy = hip
    s = math.sin(phase_angle)
    lift = max(0.0, math.cos(phase_angle)) * 2.5
    fx = hx + round(4 * s)
    fy = GROUND - round(lift)
    kx = hx + round(2 * s) + 1
    ky = hy + 3 - round(lift / 2)
    d.line([hx, hy, kx, ky], fill=color, width=2)
    d.line([kx, ky, fx, fy - 1], fill=color, width=2)
    box(d, fx - 1, fy - 1, 4, 2, SHOES)


def draw_runner(d, f, folder_pop):
    cx = 66
    a = 2 * math.pi * (f % 12) / 12
    bob = -1 if (f % 6) in (1, 2) else 0
    hip = (cx, GROUND - 8 + bob)

    # линии скорости за спиной
    for i, ly in enumerate([48, 55, 62]):
        ln = 6 + (((f // 2) + i * 2) % 4) * 2
        box(d, cx - 12 - ln, ly + bob, ln, 1, SPEEDLINE)

    leg(d, hip, a + math.pi, PANTS)          # дальняя нога
    # дальняя рука машет назад
    sx, sy = cx - 1, GROUND - 17 + bob
    swing = round(3 * math.sin(a))
    d.line([sx, sy, sx - 3 + swing, sy + 5], fill=SHIRT_DARK, width=2)
    # туловище
    box(d, cx - 3, GROUND - 18 + bob, 7, 10, SHIRT)
    box(d, cx - 3, GROUND - 10 + bob, 7, 2, PANTS)
    leg(d, hip, a, PANTS)                    # ближняя нога
    # голова
    hy = GROUND - 26 + bob
    box(d, cx - 3, hy, 7, 7, SKIN)
    box(d, cx - 4, hy - 1, 8, 3, HAIR)
    box(d, cx - 4, hy, 2, 4, HAIR)
    box(d, cx + 2, hy + 3, 1, 1, SHOES)      # глаз
    box(d, cx + 1, hy + 5, 2, 1, "#d98e7a")  # улыбка
    # папка в руках
    py = GROUND - 17 + bob - folder_pop
    box(d, cx + 4, py - 2, 4, 2, FOLDER_DARK)
    box(d, cx + 4, py, 10, 8, FOLDER)
    box(d, cx + 4, py, 10, 2, FOLDER_DARK)
    box(d, cx + 6, py - 1, 6, 1, PAPER)      # торчащий листок
    # ближняя рука держит папку
    box(d, cx + 1, GROUND - 15 + bob, 5, 2, SHIRT_DARK)
    box(d, cx + 5, GROUND - 15 + bob, 2, 2, SKIN)
    return cx + 9, py


def draw_paper(d, x, y):
    box(d, x, y, 6, 7, PAPER)
    box(d, x + 4, y, 2, 2, PAPER_LINE)
    for ly in (2, 4):
        box(d, x + 1, y + ly, 4, 1, PAPER_LINE)


def make_frames():
    tile = make_tile()
    frames = []
    for f in range(N):
        img = Image.new("RGB", (W, H), WALL)
        off = f * SPEED
        for bx in range(-off, W + TILE, TILE):
            img.paste(tile, (bx, 0))
        d = ImageDraw.Draw(img)
        # файлы летят с полок в папку: 3 штуки, со сдвигом
        papers = []
        pop = 0
        for k in range(3):
            t = ((f - 16 * k) % N) / 24
            if t <= 1:
                papers.append(t)
                if t > 0.85:
                    pop = 1
        tx, ty = draw_runner(d, f, pop)
        for k, t in enumerate(papers):
            sx, sy = W + 2, 18 + (k * 7) % 14
            x = sx + (tx - 3 - sx) * t
            y = sy + (ty - 6 - sy) * t - 16 * math.sin(math.pi * t)
            if t < 0.97:
                draw_paper(d, round(x), round(y))
        frames.append(img.resize((W * SCALE, H * SCALE), Image.NEAREST))
    return frames


if __name__ == "__main__":
    out = Path(__file__).with_name("animation.gif")
    frames = make_frames()
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=40, loop=0, disposal=2, optimize=False)
    print(f"Готово: {out} ({len(frames)} кадров)")
