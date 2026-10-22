"""Качалка для телефона: сервер.

Телефон (страница kachalka.flownote.cc/app) или Telegram-бот (bot.py рядом)
присылает ссылку, сервер качает через yt-dlp и отдаёт готовый файл. Наружу
сервер выходит через Cloudflare Tunnel (kachalka-api.flownote.cc), сам слушает
только 127.0.0.1.

Запуск: phone/run.sh. Живёт на сервере в Финляндии (systemd), запасной
вариант — Мак (launchd), см. phone/README.md. Папки и лимиты меняются
переменными окружения KACHALKA_*.
"""
import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import certifi
import yt_dlp

ENV = os.environ.get
PORT = int(ENV("KACHALKA_PORT", "8977"))
WORK = Path(ENV("KACHALKA_WORK") or Path.home() / "Library" / "Caches" / "Kachalka-phone")
STATS = Path(ENV("KACHALKA_DATA") or Path.home() / "Library" / "Application Support" / "Kachalka-phone") / "stats.json"
PUSH_KEY = STATS.parent / "push.key"                       # ключ для отправки счётчика на сайт
PUSH_URL = "https://kachalka.flownote.cc/api/phone"         # счётчики на сайте и в README
ADMIN_KEY = STATS.parent / "admin.key"                     # ключ к странице статистики /admin (только для автора)
ADMIN_PAGE = Path(__file__).with_name("admin.html")
SITE_COUNTS = "https://kachalka.flownote.cc/api/downloads"
GITHUB_API = "https://api.github.com/repos/kozliq/kachalka/releases?per_page=100"
TLS = ssl.create_default_context(cafile=certifi.where())   # у Python с python.org нет своих сертификатов
ORIGINS = {"https://kachalka.flownote.cc", "http://localhost:8931", "http://localhost:8932"}   # + локальные превью сайта

QUALITIES = ("1080p", "720p", "480p", "mp3")
LONG_TRACK = 120            # секунд: дольше — на телефоне кивает кот в наушниках
MAX_PARALLEL = int(ENV("KACHALKA_PARALLEL", "2"))   # сколько загрузок одновременно
MAX_PER_HOUR = 30           # загрузок с одного адреса в час
MAX_ACTIVE = 3              # одновременно с одного адреса (качаются + ждут)
MAX_WAITING = 25            # всего ждут очереди; больше — «сервер занят» (бот сам держит не больше 15)
QUEUE_STEP = 15             # секунд в среднем на одну загрузку — для «примерно столько ждать»
QUEUE_NOTE = ("Качалка бесплатная, а сервер у неё один, поэтому видео качаются по очереди. "
              "Обычно это минута-две — спасибо, что ждёшь.")
MAX_GB = float(ENV("KACHALKA_MAX_GB", "2"))         # больше этого не качаем
MAX_BYTES = int(MAX_GB * 1024 ** 3)
GB_TEXT = f"{MAX_GB:g}".replace(".", ",")
KEEP_FILES = 2 * 3600       # файлы живут 2 часа
RESTART_AFTER = 24 * 3600   # раз в сутки без загрузок — перезапуск (run.sh обновит yt-dlp)

STARTED = time.time()
jobs = {}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_PARALLEL)
hits = {}                   # адрес → время последних запусков


def find_ffmpeg():
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    return None


FFMPEG = find_ffmpeg()
FFPROBE = shutil.which("ffprobe") or (FFMPEG and os.path.join(os.path.dirname(FFMPEG), "ffprobe"))


def video_size(path):
    """Ширина, высота и длительность по самому файлу.

    Сайты сообщают их не всегда (TikTok — нет), а без них Telegram рисует видео квадратом.
    """
    if not (path and FFPROBE and os.path.exists(FFPROBE) and os.path.exists(path)):
        return {}
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-of", "json",
                            "-show_entries", "stream=width,height,sample_aspect_ratio"
                            ":stream_side_data=rotation:format=duration", path],
                           capture_output=True, text=True, timeout=60)
        info = json.loads(r.stdout)
        s = info["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        num, _, den = (s.get("sample_aspect_ratio") or "").partition(":")
        if num.isdigit() and den.isdigit() and int(num) and int(den):
            w = round(w * int(num) / int(den))     # неквадратные пиксели — как видео выглядит на экране
        rotation = next((int(sd["rotation"]) for sd in s.get("side_data_list") or []
                         if "rotation" in sd), 0)
        if abs(rotation) % 180 == 90:               # снято телефоном боком: в файле лёжа, на экране стоя
            w, h = h, w
        size = {"width": w, "height": h}
        duration = float((info.get("format") or {}).get("duration") or 0)
        if duration:
            size["duration"] = round(duration)
        return size
    except Exception:
        return {}


def tidy_mp4(path):
    """Переупаковка без перекодирования (как в программе): лечит TikTok и даёт faststart."""
    if not (path and FFMPEG and path.lower().endswith(".mp4") and os.path.exists(path)):
        return
    tmp = path[:-4] + ".tidy.mp4"
    try:
        r = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0",
                            "-c", "copy", "-video_track_timescale", "90000",
                            "-movflags", "+faststart", tmp],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


NET_ERRORS = ("getaddrinfo", "name or service not known", "nodename nor servname",
              "failed to resolve", "name resolution", "timed out", "connection reset",
              "connection refused", "network is unreachable", "unable to connect",
              "connection aborted", "remote end closed", "ssl")


def about(sec):
    """Сколько ждать, по-человечески: «полминуты», «минута», «3 минуты»."""
    if sec <= 30:
        return "полминуты"
    m = round(sec / 60) or 1
    if m == 1:
        return "минута"
    return f"{m} минуты" if m % 10 in (2, 3, 4) and m % 100 not in (12, 13, 14) else f"{m} минут"


def is_photo_post(error):
    """Пост в Instagram из одних фото: yt-dlp ищет в нём видео и не находит."""
    m = error.lower()
    return "[instagram]" in m and ("no video in this post" in m or "no video formats" in m)


def friendly_error(raw):
    m = raw.lower()
    if "no space left" in m:
        return "На сервере кончилось место. Попробуй через пару минут или выбери 720p."
    if "max_filesize" in m or "larger than max" in m:
        return f"Файл больше {GB_TEXT} ГБ — для телефона это слишком. Выбери 720p или 480p."
    if is_photo_post(m):
        return "В этом посте только фото, видео в нём нет."
    if "unsupported url" in m:
        return "Эта ссылка не поддерживается. Проверь, что это ссылка на само видео."
    if "sign in to confirm" in m or "not a bot" in m:
        return "YouTube просит подтвердить, что это не робот. Попробуй через пару минут."
    if "private" in m or "log in" in m or "login" in m or "sign in" in m:
        return "Видео закрытое или доступно только после входа на сайт."
    if "unavailable" in m or "not available" in m or "removed" in m:
        return "Видео недоступно: его удалили или закрыли."
    if any(s in m for s in NET_ERRORS):
        return "Сайт с видео не ответил. Попробуй ещё раз через минуту."
    if "unexpected response" in m or "unable to extract" in m or "report this issue" in m:
        return "Сайт не отдал видео. Попробуй ещё раз — или другую ссылку."
    return raw.replace("ERROR: ", "")[:160]


stats_lock = threading.Lock()


def bump_stats(job):
    """Считает удачные загрузки: всего, по дням и по сайтам. Итог уходит на сайт."""
    try:
        with stats_lock:
            STATS.parent.mkdir(parents=True, exist_ok=True)
            s = json.loads(STATS.read_text("utf-8")) if STATS.exists() else {}
            box = s.setdefault("telegram", {}) if job.source == "tg" else s   # бот считается отдельно
            day = time.strftime("%Y-%m-%d")
            box["total"] = box.get("total", 0) + 1
            days = box.setdefault("days", {})
            days[day] = days.get(day, 0) + 1
            site = re.sub(r"^(www|m|vm|vt)\.", "", re.sub(r"^https?://([^/]+).*", r"\1", job.url))
            sites = box.setdefault("sites", {})
            sites[site] = sites.get(site, 0) + 1
            users = box.setdefault("users", [])   # хэши случайных номеров телефонов / id в Telegram
            if job.uid:
                if job.uid not in users:
                    users.append(job.uid)
                    new = box.setdefault("new", {})       # сколько новых людей пришло за день
                    new[day] = new.get(day, 0) + 1
                box.setdefault("seen", {})[job.uid] = day  # когда человек качал последний раз
            STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
        if job.source != "tg":
            push_total(s["total"], len(s["users"]))
    except Exception as e:
        print("stats:", e, flush=True)


gh_cache = {"at": 0, "rs": None}


def github_releases():
    """Релизы программы на GitHub с числом скачиваний. Помним 5 минут — у GitHub лимит запросов."""
    if gh_cache["rs"] is None or time.time() - gh_cache["at"] > 300:
        req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "kachalka-phone",
                                                          "Accept": "application/vnd.github+json"})
        gh_cache.update(rs=json.loads(urllib.request.urlopen(req, timeout=20, context=TLS).read()),
                        at=time.time())
    return gh_cache["rs"]


def github_counts():
    """Сколько раз скачали программу с GitHub и последняя версия.

    Cloudflare сам у GitHub спросить не может (общий лимит запросов на его адреса),
    поэтому спрашивает сервер Качалки и отправляет на сайт вместе с телефонными числами.
    """
    try:
        rs = github_releases()
        count = sum(a["download_count"] for r in rs for a in r.get("assets", []))
        return {"github": count, "version": (rs[0]["tag_name"] if rs else "").lstrip("v")}
    except Exception as e:
        print("github:", e, flush=True)
        return {}


def push_total(videos, users, with_github=False):
    """Отправляет на сайт, сколько видео скачали и сколько разных телефонов (kachalka.flownote.cc/api/phone)."""
    try:
        key = PUSH_KEY.read_text("utf-8").strip()
    except Exception:
        return

    def go():
        try:
            body = {"videos": videos, "users": users, **(github_counts() if with_github else {})}
            req = urllib.request.Request(
                PUSH_URL, data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "User-Agent": "kachalka-phone"})
            urllib.request.urlopen(req, timeout=20, context=TLS).read()
        except Exception as e:
            print("push:", e, flush=True)

    threading.Thread(target=go, daemon=True).start()


def make_room(current):
    """Мало места на диске — удаляет самые старые готовые загрузки (на сервере диск маленький)."""
    need = 2 * MAX_BYTES + 512 * 1024 ** 2     # склейка и переупаковка держат две копии файла
    with jobs_lock:
        old = sorted((j for j in jobs.values() if j is not current and j.state in ("done", "error")),
                     key=lambda j: j.created)
    for j in old:
        if shutil.disk_usage(WORK).free >= need:
            break
        shutil.rmtree(j.dir, ignore_errors=True)
        with jobs_lock:
            jobs.pop(j.id, None)


class Job:
    def __init__(self, url, quality, uid="", fit=0, source=""):
        self.id = uuid.uuid4().hex[:12]
        self.uid = hashlib.sha256(uid.encode()).hexdigest()[:16] if uid else ""
        self.url = url
        self.quality = quality
        self.audio = quality == "mp3"
        self.height = 0 if self.audio else int(quality.rstrip("p"))
        self.fit = fit              # байт: подобрать качество, чтобы файл влез (Telegram — до 50 МБ)
        self.source = source        # "tg" — пришло из Telegram-бота
        self.who = ""               # адрес (у бота — tg:человек), для лимитов
        self.size_info = {}         # ширина, высота, длительность — боту для плеера в Telegram
        self.dir = WORK / self.id
        self.created = time.time()
        self.state = "queued"       # queued → working → done | error
        self.stage = "Жду очереди…"
        self.progress = 0.0
        self.speed = None
        self.eta = None
        self.title = ""
        self.long = False
        self.error = ""
        self.path = None
        self.weights = {}
        self.done_share = 0.0

    def public(self):
        d = {"id": self.id, "state": self.state, "stage": self.stage,
             "progress": round(self.progress, 3), "speed": self.speed, "eta": self.eta,
             "title": self.title, "audio": self.audio, "long": self.long, "error": self.error}
        if self.state == "queued":      # сколько загрузок впереди — чтобы не казалось, что всё зависло
            with jobs_lock:
                ahead = sum(j.state == "working" or (j.state == "queued" and j.created < self.created)
                            for j in jobs.values())
            if ahead:
                d["ahead"] = ahead
                d["stage"] = f"Ты в очереди: впереди {ahead}, это примерно {about(ahead * QUEUE_STEP)}"
                d["note"] = QUEUE_NOTE
        if self.path and os.path.exists(self.path):
            d["name"] = os.path.basename(self.path)
            d["size"] = os.path.getsize(self.path)
            d.update(self.size_info)
        return d

    def hook(self, d):
        fid = (d.get("info_dict") or {}).get("format_id")
        share = self.weights.get(fid, 1.0 / max(len(self.weights), 1))
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            part = d.get("downloaded_bytes", 0) / total if total else 0
            self.progress = min(self.done_share + share * part, 1.0) * 0.97
            self.speed, self.eta = d.get("speed"), d.get("eta")
        elif d["status"] == "finished":
            self.done_share = min(self.done_share + share, 1.0)
            self.progress = self.done_share * 0.97
            self.speed = self.eta = None

    def pp_hook(self, d):
        if d["status"] != "started":
            return
        if d.get("postprocessor") == "Merger":
            self.stage = "Склеиваю видео и звук…"
        elif d.get("postprocessor") == "ExtractAudio":
            self.stage = "Перегоняю в MP3…"

    def run(self):
        with slots:
            self.state = "working"
            self.stage = "Ищу видео по ссылке…"
            make_room(self)
            caffeinate = None
            try:   # пока качаем, Мак не засыпает (на Linux caffeinate нет — и не надо)
                caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            except Exception:
                pass
            try:
                self.download()
                self.state = "done"
                self.stage = "Готово"
                bump_stats(self)
            except yt_dlp.utils.DownloadError as e:
                self.state, self.error = "error", friendly_error(str(e))
            except Exception as e:
                self.state, self.error = "error", f"{type(e).__name__}: {e}"[:160]
            finally:
                if caffeinate:
                    caffeinate.terminate()

    def download(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        opts = {
            # как в программе: H.264 + AAC в mp4 — на айфоне сохраняется прямо в Фото
            "format": "bv*+ba/b",
            "format_sort": ["vcodec:h264", f"res:{self.height}", "acodec:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": str(self.dir / "%(title).80s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "max_filesize": MAX_BYTES,
            "progress_hooks": [self.hook],
            "postprocessor_hooks": [self.pp_hook],
        }
        if self.audio:
            del opts["format_sort"], opts["merge_output_format"]
            opts["format"] = "ba/b"
            opts["writethumbnail"] = True
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
        if FFMPEG:
            opts["ffmpeg_location"] = FFMPEG
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
            if info.get("_type") == "playlist":
                info = next((e for e in info.get("entries") or [] if e), None)
                if not info:
                    raise yt_dlp.utils.DownloadError("В плейлисте нет видео")
            if self.fit and not self.audio:
                info = self.fit_quality(ydl, info)
            parts = info.get("requested_formats") or [info]
            sizes = [p.get("filesize") or p.get("filesize_approx") or 0 for p in parts]
            total = sum(sizes)
            for p, s in zip(parts, sizes):
                self.weights[p.get("format_id")] = s / total if total else 1 / len(parts)
            self.title = (info.get("title") or "")[:120]
            self.long = self.audio and (info.get("duration") or 0) > LONG_TRACK
            self.stage = "Качаю звук" if self.audio else "Качаю видео"
            result = ydl.process_ie_result(info, download=True)
        self.size_info = {k: result[k] for k in ("width", "height", "duration")
                          if isinstance((result or {}).get(k), (int, float))}
        path = None
        for rd in (result or {}).get("requested_downloads") or []:
            path = rd.get("filepath") or path
        if self.audio:
            mp3 = os.path.splitext(path)[0] + ".mp3" if path else None
            path = mp3 if mp3 and os.path.exists(mp3) else path
        else:
            self.stage = "Привожу файл в порядок…"
            tidy_mp4(path)
            self.size_info.update(video_size(path))
        if not path or not os.path.exists(path):
            raise RuntimeError("файл не получился")
        self.path = path
        self.progress = 1.0

    def fit_quality(self, ydl, info):
        """Самое чёткое качество, которое влезает в self.fit байт (для Telegram-бота).

        Форматы уже получены, перебор качества идёт без новых запросов к сайту.
        Ничего не влезло или размер неизвестен — 720p, бот тогда даст ссылку.
        """
        def size(i):
            sizes = [p.get("filesize") or p.get("filesize_approx") or 0
                     for p in i.get("requested_formats") or [i]]
            return sum(sizes) if all(sizes) else 0

        def pick(h):
            # звук обычный стерео (не 5.1 на 384 kbps — он весит как половина видео)
            ydl.params["format_sort"] = ["vcodec:h264", f"res:{h}", "acodec:m4a", "abr:160"]
            fresh = copy.deepcopy(info)
            fresh.pop("requested_formats", None)    # иначе останутся дорожки прошлого выбора
            return ydl.process_ie_result(fresh, download=False)

        for h in (1080, 720, 480, 360):
            if h > self.height:
                continue
            try:
                cand = pick(h)
            except Exception:
                continue
            w, hh = cand.get("width") or 0, cand.get("height") or 0
            short_side = min(w, hh) if w and hh else hh     # у вертикальных Shorts и TikTok — ширина
            if short_side <= h and 0 < size(cand) <= self.fit:
                return cand
        return pick(min(720, self.height))


def phone_numbers():
    try:
        st = json.loads(STATS.read_text("utf-8"))
        return st.get("total", 0), len(st.get("users", []))
    except Exception:
        return 0, 0


def admin_numbers():
    """Всё для страницы /admin: программа, телефон, бот и по дням."""
    try:
        s = json.loads(STATS.read_text("utf-8"))
    except Exception:
        s = {}
    today = time.strftime("%Y-%m-%d")
    week = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))

    def part(b):
        days, seen = b.get("days", {}), b.get("seen", {}).values()
        return {"videos": b.get("total", 0), "users": len(b.get("users", [])),
                "today": {"videos": days.get(today, 0), "users": sum(d == today for d in seen)},
                "week": {"videos": sum(n for d, n in days.items() if d >= week),
                         "users": sum(d >= week for d in seen)},
                "sites": b.get("sites", {})}

    tg = s.get("telegram", {})
    days = sorted(set(s.get("days", {})) | set(tg.get("days", {})), reverse=True)[:30]
    out = {"time": time.strftime("%H:%M"), "today": today, "phone": part(s), "bot": part(tg),
           "days": [{"day": d, "phone": s.get("days", {}).get(d, 0), "phone_new": s.get("new", {}).get(d, 0),
                     "bot": tg.get("days", {}).get(d, 0), "bot_new": tg.get("new", {}).get(d, 0)} for d in days],
           "yt_dlp": yt_dlp.version.__version__, "up": int(time.time() - STARTED), "program": None}
    with jobs_lock:
        out["busy"] = sum(j.state in ("queued", "working") for j in jobs.values())
    try:
        releases = []
        for r in github_releases():
            rel = {"version": r["tag_name"].lstrip("v"), "date": (r.get("published_at") or "")[:10], "win": 0, "mac": 0}
            for a in r.get("assets", []):
                rel["mac" if "mac" in a["name"].lower() else "win"] += a["download_count"]
            releases.append(rel)
        out["program"] = {"total": sum(r["win"] + r["mac"] for r in releases), "releases": releases,
                          "win": sum(r["win"] for r in releases), "mac": sum(r["mac"] for r in releases)}
        req = urllib.request.Request(SITE_COUNTS, headers={"User-Agent": "kachalka-phone"})
        out["program"]["rickrolled"] = json.loads(urllib.request.urlopen(req, timeout=5, context=TLS).read())["rickrolled"]
    except Exception as e:
        print("admin:", e, flush=True)
    return out


def janitor():
    """Чистит старые файлы, обновляет счётчики на сайте и раз в сутки перезапускает сервер."""
    while True:
        push_total(*phone_numbers(), with_github=True)
        time.sleep(600)
        now = time.time()
        with jobs_lock:
            for jid, j in list(jobs.items()):
                if now - j.created > KEEP_FILES and j.state in ("done", "error"):
                    shutil.rmtree(j.dir, ignore_errors=True)
                    del jobs[jid]
            busy = any(j.state in ("queued", "working") for j in jobs.values())
        for ip in list(hits):
            hits[ip] = [t for t in hits[ip] if now - t < 3600]
            if not hits[ip]:
                del hits[ip]
        if now - STARTED > RESTART_AFTER and not busy:
            os._exit(0)     # systemd (или launchd на Маке) поднимет заново, run.sh обновит yt-dlp


URL_RE = re.compile(r"https?://[^\s<>\"']+")


class Handler(BaseHTTPRequestHandler):
    server_version = "Kachalka"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length")

    def json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def is_admin(self):
        try:
            key = ADMIN_KEY.read_text("utf-8").strip()
        except Exception:
            return False
        got = self.headers.get("Authorization", "")
        return bool(key) and hmac.compare_digest(got.encode(), f"Bearer {key}".encode())

    def client_ip(self):
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def do_POST(self):
        if self.path != "/api/jobs":
            return self.json(404, {"error": "нет такого адреса"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
        except Exception:
            return self.json(400, {"error": "Не понял запрос"})
        m = URL_RE.search(str(data.get("url", "")))
        quality = data.get("quality") if data.get("quality") in QUALITIES else "1080p"
        if not m:
            return self.json(400, {"error": "Не вижу ссылки. Скопируй ссылку на видео и вставь сюда."})
        ip, now = self.client_ip(), time.time()
        uid = data.get("uid") if isinstance(data.get("uid"), str) else ""
        # бот живёт на этом же сервере: его запросы без заголовка Cloudflare, лимит — на человека
        source = "tg" if data.get("from") == "tg" and not self.headers.get("CF-Connecting-IP") else ""
        if source:
            ip = "tg:" + uid
        fit = data.get("fit") if isinstance(data.get("fit"), int) and data["fit"] > 0 else 0
        job = Job(m.group(0), quality, uid[:64], fit, source)
        job.who = ip
        with jobs_lock:     # проверка и запись разом — иначе толпа одновременных запросов проскочит
            live = [j for j in jobs.values() if j.state in ("queued", "working")]
            recent = [t for t in hits.get(ip, []) if now - t < 3600]
            if sum(j.state == "queued" for j in live) >= MAX_WAITING:
                error = 503, "Сейчас очень много желающих, а Качалка бесплатная и сервер у неё один. Пришли ссылку через пару минут — очередь быстро разойдётся."
            elif sum(j.who == ip for j in live) >= MAX_ACTIVE:
                error = 429, "Сначала дождись прошлых загрузок — потом пришли эту."
            elif len(recent) >= MAX_PER_HOUR:
                error = 429, "Слишком много загрузок за час. Передохни немного."
            else:
                error = None
                hits[ip] = recent + [now]
                jobs[job.id] = job
        if error:
            return self.json(error[0], {"error": error[1]})
        threading.Thread(target=job.run, daemon=True).start()
        return self.json(200, job.public())

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = self.path.split("?")[0]
        if path == "/api/ping":
            with jobs_lock:
                busy = sum(j.state == "working" for j in jobs.values())
            return self.json(200, {"ok": True, "busy": busy, "yt_dlp": yt_dlp.version.__version__})
        if path == "/admin":     # страница статистики для автора; без ключа она пустая
            return self.send_page(head)
        if path == "/api/admin":
            if not self.is_admin():
                return self.json(403, {"error": "нет доступа"})
            return self.json(200, admin_numbers())
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})(/file)?", path)
        job = jobs.get(m.group(1)) if m else None
        if not job:
            return self.json(404, {"error": "Загрузка не найдена — возможно, файл уже удалён. Скачай заново."})
        if not m.group(2):
            return self.json(200, job.public())
        if job.state != "done" or not job.path or not os.path.exists(job.path):
            return self.json(409, {"error": "Файл ещё не готов"})
        self.send_file(job, head)

    def send_page(self, head):
        body = ADMIN_PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def send_file(self, job, head):
        size = os.path.getsize(job.path)
        name = os.path.basename(job.path)
        ctype = "audio/mpeg" if name.lower().endswith(".mp3") else "video/mp4"
        start, end = 0, size - 1
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(size - int(rng.group(2)), 0)
            if start > end:
                self.send_response(416)
                self.cors()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.cors()
        ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        if head:
            return
        with open(job.path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # туннель сам закрывает простаивающие соединения — это не ошибка, не мусорим в лог
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    shutil.rmtree(WORK, ignore_errors=True)     # хвосты прошлого запуска
    WORK.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=janitor, daemon=True).start()
    srv = Server(("127.0.0.1", PORT), Handler)
    print(f"Качалка-сервер на 127.0.0.1:{PORT}, yt-dlp {yt_dlp.version.__version__}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
