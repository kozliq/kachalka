"""Качалка видео: вставил ссылку → нажал «Скачать» → mp4 на Рабочем столе.

Видео сохраняется так, чтобы открывалось в QuickTime (H.264 + AAC в mp4).
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageSequence
import yt_dlp

# внутри собранной программы (exe / .app) файлы лежат во временной папке
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
GIF_PATH = APP_DIR / "animation.gif"
CONFIG = Path.home() / ".kachalka.json"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def desktop_dir():
    """Настоящий Рабочий стол (на Windows он бывает внутри OneDrive)."""
    if IS_WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
            if buf.value and os.path.isdir(buf.value):
                return Path(buf.value)
        except Exception:
            pass
    d = Path.home() / "Desktop"
    return d if d.exists() else Path.home()


def load_out_dir():
    try:
        saved = json.loads(CONFIG.read_text("utf-8")).get("out_dir")
        if saved:
            return Path(saved)
    except Exception:
        pass
    return desktop_dir() / "Качалка"


def save_out_dir(path):
    try:
        CONFIG.write_text(json.dumps({"out_dir": str(path)}, ensure_ascii=False), "utf-8")
    except Exception:
        pass


OUT_DIR = load_out_dir()

# в собранной программе без консоли stdout/stderr пустые — yt-dlp на это падает
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# цвета
BG = "#14101c"
PANEL = "#1f1829"
BORDER = "#3a2f52"
TEXT = "#f0ebee"
TEXT_2 = "#b8afb6"
ACCENT = "#7c3aed"
ACCENT_HOVER = "#8b5cf6"
BAR = "#a78bfa"
OK = "#7fd88f"
ERR = "#ff8a8a"

GIF_W, GIF_H = 480, 240


def find_ffmpeg():
    """Сначала свой ffmpeg из комплекта программы, потом системный."""
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    return None


def fmt_time(sec):
    if sec is None:
        return ""
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_speed(bps):
    if not bps:
        return ""
    mb = bps / 1024 / 1024
    return f"{mb:.1f} МБ/с" if mb >= 1 else f"{bps / 1024:.0f} КБ/с"


class Downloader(threading.Thread):
    """Качает в фоне и шлёт события в очередь, окно их читает."""

    def __init__(self, url, events):
        super().__init__(daemon=True)
        self.url = url
        self.events = events
        self.weights = {}       # format_id → доля от общего размера
        self.done_share = 0.0   # сколько уже скачано целыми частями
        self.current = None

    def send(self, kind, **data):
        self.events.put((kind, data))

    def hook(self, d):
        fid = (d.get("info_dict") or {}).get("format_id")
        share = self.weights.get(fid, 1.0 / max(len(self.weights), 1))
        if d["status"] == "downloading":
            if fid != self.current:
                self.current = fid
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            part = d.get("downloaded_bytes", 0) / total if total else 0
            overall = min(self.done_share + share * part, 1.0)
            self.send("progress", value=overall * 0.97,
                      speed=d.get("speed"), eta=d.get("eta"))
        elif d["status"] == "finished":
            self.done_share = min(self.done_share + share, 1.0)
            self.send("progress", value=self.done_share * 0.97, speed=None, eta=None)

    def pp_hook(self, d):
        if d["status"] == "started" and d.get("postprocessor") == "Merger":
            self.send("stage", text="Склеиваю видео и звук…")

    def run(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        opts = {
            # эквивалент: -S "vcodec:h264,res,acodec:m4a" --merge-output-format mp4
            "format": "bv*+ba/b",
            "format_sort": ["vcodec:h264", "res", "acodec:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": str(OUT_DIR / "%(title).80s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [self.hook],
            "postprocessor_hooks": [self.pp_hook],
        }
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                self.send("stage", text="Ищу видео по ссылке…")
                info = ydl.extract_info(self.url, download=False)
                if info.get("_type") == "playlist":
                    info = next((e for e in info.get("entries") or [] if e), None)
                    if not info:
                        raise yt_dlp.utils.DownloadError("В плейлисте нет видео")
                parts = info.get("requested_formats") or [info]
                sizes = [p.get("filesize") or p.get("filesize_approx") or 0 for p in parts]
                total = sum(sizes)
                for p, s in zip(parts, sizes):
                    self.weights[p.get("format_id")] = s / total if total else 1 / len(parts)
                self.send("stage", text=f"Качаю: {info.get('title', 'видео')[:60]}")
                result = ydl.process_ie_result(info, download=True)
                path = None
                for rd in (result or {}).get("requested_downloads") or []:
                    path = rd.get("filepath") or path
                self.send("done", path=path)
        except yt_dlp.utils.DownloadError as e:
            msg = str(e).replace("ERROR: ", "")
            self.send("error", text=msg[:160])
        except Exception as e:  # чтобы окно не зависло на любой ошибке
            self.send("error", text=f"{type(e).__name__}: {e}"[:160])


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Качалка")
        self.configure(fg_color=BG)
        self.resizable(False, False)

        self.events = queue.Queue()
        self.worker = None
        self.target = 0.0
        self.shown = 0.0
        self.last_path = None
        self.frames, self.delays = self.load_gif()
        self.frame_i = 0
        self.anim_job = None

        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.build_ui()
        self.prefill_from_clipboard()
        self.center()
        self.after(16, self.tick)

    def center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = max(30, (self.winfo_screenheight() - h) // 2 - 40)
        self.geometry(f"+{x}+{y}")

    # ---------- интерфейс ----------
    def build_ui(self):
        pad = 24
        wrap = ctk.CTkFrame(self, fg_color=BG)
        wrap.pack(padx=pad, pady=pad)

        ctk.CTkLabel(wrap, text="Качалка", text_color=TEXT,
                     font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(wrap, text="YouTube, TikTok, VK, Instagram — в mp4, открывается везде",
                     text_color=TEXT_2, font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(2, 16))

        row = ctk.CTkFrame(wrap, fg_color=BG)
        row.pack(fill="x")
        self.entry = ctk.CTkEntry(row, placeholder_text="Вставь ссылку на видео",
                                  height=40, corner_radius=6, border_width=1,
                                  fg_color=PANEL, border_color=BORDER, text_color=TEXT,
                                  placeholder_text_color=TEXT_2,
                                  font=ctk.CTkFont(size=15))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.start())
        ctk.CTkButton(row, text="Вставить", width=96, height=40, corner_radius=6,
                      fg_color=PANEL, hover_color=BORDER, border_width=1,
                      border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=15),
                      command=self.paste).pack(side="left", padx=(8, 0))

        self.btn = ctk.CTkButton(wrap, text="Скачать", height=44, corner_radius=6,
                                 fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                 text_color="#ffffff", text_color_disabled="#ffffff",
                                 font=ctk.CTkFont(size=16, weight="bold"),
                                 command=self.start)
        self.btn.pack(fill="x", pady=(12, 16))

        # область анимации: фиксированного размера, чтобы окно не прыгало
        self.stage = ctk.CTkFrame(wrap, width=GIF_W, height=GIF_H, corner_radius=6,
                                  fg_color=PANEL, border_width=1, border_color=BORDER)
        self.stage.pack()
        self.stage.pack_propagate(False)
        self.idle = ctk.CTkLabel(self.stage, text="Вставь ссылку —\nи челик побежит за файлами",
                                 text_color=TEXT_2, font=ctk.CTkFont(size=15))
        self.idle.place(relx=0.5, rely=0.5, anchor="center")
        self.gif = ctk.CTkLabel(self.stage, text="")

        self.bar = ctk.CTkProgressBar(wrap, height=8, corner_radius=4,
                                      fg_color=PANEL, progress_color=BAR)
        self.bar.set(0)
        self.bar.pack(fill="x", pady=(16, 10))

        bottom = ctk.CTkFrame(wrap, fg_color=BG)
        bottom.pack(fill="x")
        self.status = ctk.CTkLabel(bottom, text=self.folder_hint(),
                                   text_color=TEXT_2, font=ctk.CTkFont(size=15),
                                   anchor="w", justify="left", wraplength=250)
        self.status.pack(side="left", fill="x", expand=True)
        self.open_btn = ctk.CTkButton(bottom, text="Открыть папку", width=120, height=32,
                                      corner_radius=6, fg_color=PANEL, hover_color=BORDER,
                                      border_width=1, border_color=BORDER, text_color=TEXT,
                                      font=ctk.CTkFont(size=13), command=self.open_folder)
        self.open_btn.pack(side="right")
        ctk.CTkButton(bottom, text="Сменить", width=84, height=32,
                      corner_radius=6, fg_color=PANEL, hover_color=BORDER,
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=13),
                      command=self.choose_folder).pack(side="right", padx=(0, 8))

    def load_gif(self):
        if not GIF_PATH.exists():
            return [], []
        frames, delays = [], []
        src = Image.open(GIF_PATH)
        for fr in ImageSequence.Iterator(src):
            img = fr.convert("RGBA")
            # вписываем в область, сохраняя пропорции
            k = min(GIF_W / img.width, GIF_H / img.height)
            size = (int(img.width * k), int(img.height * k))
            # заранее увеличиваем ×2 «по пикселям» — на Retina не будет мыла
            big = img.resize((size[0] * 2, size[1] * 2), Image.NEAREST)
            frames.append(ctk.CTkImage(light_image=big, dark_image=big, size=size))
            delays.append(max(fr.info.get("duration", 80), 20))
        return frames, delays

    # ---------- гифка ----------
    def show_gif(self):
        if not self.frames:
            return
        self.idle.place_forget()
        self.gif.place(relx=0.5, rely=0.5, anchor="center")
        self.frame_i = 0
        self.animate()

    def hide_gif(self, idle_text):
        if self.anim_job:
            self.after_cancel(self.anim_job)
            self.anim_job = None
        self.gif.place_forget()
        self.idle.configure(text=idle_text)
        self.idle.place(relx=0.5, rely=0.5, anchor="center")

    def animate(self):
        self.gif.configure(image=self.frames[self.frame_i])
        delay = self.delays[self.frame_i]
        self.frame_i = (self.frame_i + 1) % len(self.frames)
        self.anim_job = self.after(delay, self.animate)

    # ---------- действия ----------
    def paste(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        self.entry.delete(0, "end")
        self.entry.insert(0, text)

    def prefill_from_clipboard(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if text.startswith("http") and "\n" not in text:
            self.entry.insert(0, text)

    def folder_hint(self):
        home = str(Path.home())
        p = str(OUT_DIR)
        short = "~" + p[len(home):] if p.startswith(home) else p
        return f"Сохраняю в {short}"

    def choose_folder(self):
        global OUT_DIR
        if self.worker and self.worker.is_alive():
            return
        picked = filedialog.askdirectory(initialdir=str(OUT_DIR), title="Куда сохранять видео")
        if picked:
            OUT_DIR = Path(picked)
            save_out_dir(OUT_DIR)
            self.last_path = None
            self.set_status(self.folder_hint())

    def open_folder(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        has_file = self.last_path and os.path.exists(self.last_path)
        if IS_WIN:
            if has_file:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_path)])
            else:
                os.startfile(OUT_DIR)
        elif IS_MAC:
            subprocess.run(["open", "-R", self.last_path] if has_file else ["open", str(OUT_DIR)])
        else:
            subprocess.run(["xdg-open", str(OUT_DIR)])

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        url = self.entry.get().strip()
        if not url.startswith("http"):
            self.set_status("Нужна ссылка, которая начинается с http", ERR)
            return
        self.target = self.shown = 0.0
        self.bar.set(0)
        self.btn.configure(state="disabled", text="Качаю…")
        self.set_status("Начинаю…", TEXT)
        self.show_gif()
        self.worker = Downloader(url, self.events)
        self.worker.start()

    def set_status(self, text, color=TEXT_2):
        self.status.configure(text=text, text_color=color)

    def finish(self):
        self.btn.configure(state="normal", text="Скачать")

    # ---------- цикл окна: события из потока + плавный прогресс ----------
    def tick(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "stage":
                    self.stage_text = data["text"]
                    self.set_status(data["text"], TEXT)
                elif kind == "progress":
                    self.target = max(self.target, data["value"])
                    bits = [f"{int(self.target / 0.97 * 100)}%"]
                    if data.get("speed"):
                        bits.append(fmt_speed(data["speed"]))
                    if data.get("eta"):
                        bits.append(f"осталось {fmt_time(data['eta'])}")
                    self.set_status(" · ".join(bits), TEXT)
                elif kind == "done":
                    self.target = 1.0
                    self.last_path = data.get("path")
                    name = Path(self.last_path).name if self.last_path else "видео"
                    self.set_status(f"Готово: {name}", OK)
                    self.hide_gif("Готово ✓\nМожно качать следующее")
                    self.finish()
                elif kind == "error":
                    self.target = self.shown = 0.0
                    self.bar.set(0)
                    self.set_status(f"Не получилось: {data['text']}", ERR)
                    self.hide_gif("Что-то пошло не так.\nПроверь ссылку и попробуй ещё раз")
                    self.finish()
        except queue.Empty:
            pass

        if abs(self.target - self.shown) > 0.001:
            self.shown += (self.target - self.shown) * 0.12
            self.bar.set(self.shown)
        self.after(16, self.tick)


if __name__ == "__main__":
    app = App()
    # можно передать ссылку при запуске: python main.py <ссылка>
    if len(sys.argv) > 1:
        app.entry.delete(0, "end")
        app.entry.insert(0, sys.argv[1])
        app.after(400, app.start)
    app.mainloop()
