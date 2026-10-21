"""Качалка видео: вставил ссылку → нажал «Скачать» → mp4 на Рабочем столе.

Видео сохраняется так, чтобы открывалось в QuickTime (H.264 + AAC в mp4).
В режиме «MP3» качается только звук и перегоняется в mp3 с обложкой.
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
CAT_PATH = APP_DIR / "cat.gif"          # котик в наушниках — пока качается звук
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


AUDIO = "MP3"
QUALITIES = ["1080p", "720p", "480p", AUDIO]


def load_config():
    try:
        return json.loads(CONFIG.read_text("utf-8"))
    except Exception:
        return {}


def save_config(**values):
    cfg = load_config()
    cfg.update(values)
    try:
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
    except Exception:
        pass


_cfg = load_config()
OUT_DIR = Path(_cfg["out_dir"]) if _cfg.get("out_dir") else desktop_dir() / "Качалка"
QUALITY = _cfg.get("quality") if _cfg.get("quality") in QUALITIES else QUALITIES[0]

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
IDLE_VIDEO = "Вставь ссылку —\nи челик побежит за файлами"
IDLE_AUDIO = "Вставь ссылку —\nи котик врубит музыку"


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


def tidy_mp4(path, ffmpeg):
    """Переупаковывает mp4 без перекодирования.

    TikTok иногда пишет видеодорожку со странным масштабом времени,
    и QuickTime/Finder видят в файле только звук. Переупаковка это лечит.
    """
    if not (path and ffmpeg and path.lower().endswith(".mp4") and os.path.exists(path)):
        return
    tmp = path[:-4] + ".tidy.mp4"
    flags = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW: без чёрного окна на Windows
    try:
        r = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0",
                            "-c", "copy", "-video_track_timescale", "90000",
                            "-movflags", "+faststart", tmp],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=flags, timeout=600)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


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


NET_ERRORS = ("getaddrinfo", "name or service not known", "nodename nor servname",
              "failed to resolve", "name resolution", "timed out", "connection reset",
              "connection refused", "network is unreachable", "unable to connect",
              "connection aborted", "remote end closed", "ssl")
VPN_HINT = "Включи VPN для всего компьютера (не расширение в браузере) и попробуй ещё раз."


def friendly_error(raw):
    """Переводит ошибку yt-dlp на понятный язык."""
    m = raw.lower()
    if any(s in m for s in NET_ERRORS):
        return "Нет связи с сайтом — похоже, он заблокирован у провайдера. " + VPN_HINT
    if "sign in to confirm" in m or "not a bot" in m:
        return "YouTube просит подтвердить, что ты не робот. Смени сервер VPN или попробуй позже."
    if "unsupported url" in m:
        return "Эта ссылка не поддерживается. Проверь, что это ссылка на само видео."
    if "private" in m or "log in" in m or "login" in m or "sign in" in m:
        return "Видео закрытое или доступно только после входа на сайт."
    if "unavailable" in m or "not available" in m or "removed" in m:
        return "Видео недоступно: его удалили или закрыли в твоей стране. " + VPN_HINT
    if "unexpected response" in m or "unable to extract" in m or "report this issue" in m:
        return "Сайт не отдал видео — так бывает из-за блокировок. " + VPN_HINT + \
               " Не помогло — скачай свежую версию Качалки."
    return raw.replace("ERROR: ", "")[:140]


class Downloader(threading.Thread):
    """Качает в фоне и шлёт события в очередь, окно их читает."""

    def __init__(self, url, events, quality="1080p"):
        super().__init__(daemon=True)
        self.url = url
        self.events = events
        self.audio = quality == AUDIO
        self.height = 0 if self.audio else int(quality.rstrip("p"))
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
        if d["status"] != "started":
            return
        if d.get("postprocessor") == "Merger":
            self.send("stage", text="Склеиваю видео и звук…")
        elif d.get("postprocessor") == "ExtractAudio":
            self.send("stage", text="Перегоняю в MP3…")

    def run(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        opts = {
            # эквивалент: -S "vcodec:h264,res:720,acodec:m4a" --merge-output-format mp4
            # res — по меньшей стороне, так что вертикальные видео тоже считаются верно
            "format": "bv*+ba/b",
            "format_sort": ["vcodec:h264", f"res:{self.height}", "acodec:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": str(OUT_DIR / "%(title).80s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [self.hook],
            "postprocessor_hooks": [self.pp_hook],
        }
        if self.audio:
            # эквивалент: -f "ba/b" -x --audio-format mp3 --audio-quality 320K
            #             --embed-metadata --embed-thumbnail
            del opts["format_sort"], opts["merge_output_format"]
            opts["format"] = "ba/b"
            opts["writethumbnail"] = True
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
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
                what = "звук" if self.audio else "видео"
                self.send("stage", text=f"Качаю {what}: {info.get('title', what)[:60]}")
                result = ydl.process_ie_result(info, download=True)
                path = None
                for rd in (result or {}).get("requested_downloads") or []:
                    path = rd.get("filepath") or path
                if self.audio:
                    mp3 = os.path.splitext(path)[0] + ".mp3" if path else None
                    path = mp3 if mp3 and os.path.exists(mp3) else path
                else:
                    self.send("stage", text="Привожу файл в порядок…")
                    tidy_mp4(path, ffmpeg)
                self.send("done", path=path)
        except yt_dlp.utils.DownloadError as e:
            self.send("error", text=friendly_error(str(e)))
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
        self.runner = self.load_gif(GIF_PATH)
        self.cat = self.load_gif(CAT_PATH)
        self.frames, self.delays = self.runner
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
        ctk.CTkLabel(wrap, text="YouTube, TikTok, VK, Instagram — видео в mp4 или звук в mp3",
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

        row2 = ctk.CTkFrame(wrap, fg_color=BG)
        row2.pack(fill="x", pady=(12, 16))
        self.quality = ctk.CTkSegmentedButton(
            row2, values=QUALITIES, height=44, width=290, corner_radius=6,
            fg_color=PANEL, selected_color=BORDER, selected_hover_color=BORDER,
            unselected_color=PANEL, unselected_hover_color="#2a2236",
            text_color=TEXT, text_color_disabled=TEXT_2,
            font=ctk.CTkFont(size=15), command=self.set_quality)
        self.quality.set(QUALITY)
        self.quality.pack(side="left")
        self.btn = ctk.CTkButton(row2, text="Скачать", height=44, corner_radius=6,
                                 fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                 text_color="#ffffff", text_color_disabled="#ffffff",
                                 font=ctk.CTkFont(size=16, weight="bold"),
                                 command=self.start)
        self.btn.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # область анимации: фиксированного размера, чтобы окно не прыгало
        self.stage = ctk.CTkFrame(wrap, width=GIF_W, height=GIF_H, corner_radius=6,
                                  fg_color=PANEL, border_width=1, border_color=BORDER)
        self.stage.pack()
        self.stage.pack_propagate(False)
        self.idle = ctk.CTkLabel(self.stage, text=IDLE_AUDIO if QUALITY == AUDIO else IDLE_VIDEO,
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

    def load_gif(self, path):
        if not path.exists():
            return [], []
        frames, delays = [], []
        src = Image.open(path)
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
    def show_gif(self, audio=False):
        self.frames, self.delays = self.cat if audio and self.cat[0] else self.runner
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
            save_config(out_dir=str(OUT_DIR))
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

    def set_quality(self, value):
        save_config(quality=value)
        if self.idle.cget("text") in (IDLE_VIDEO, IDLE_AUDIO):
            self.idle.configure(text=IDLE_AUDIO if value == AUDIO else IDLE_VIDEO)

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
        self.quality.configure(state="disabled")
        self.set_status("Начинаю…", TEXT)
        self.show_gif(audio=self.quality.get() == AUDIO)
        self.worker = Downloader(url, self.events, self.quality.get())
        self.worker.start()

    def set_status(self, text, color=TEXT_2):
        self.status.configure(text=text, text_color=color)

    def finish(self):
        self.btn.configure(state="normal", text="Скачать")
        self.quality.configure(state="normal")

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
                    self.set_status(data["text"], ERR)
                    self.hide_gif("Не получилось скачать.\nЧто случилось — написано ниже")
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
