"""Качалка для телефона: сервер на Маке.

Телефон (страница kachalka.flownote.cc/app) присылает ссылку, сервер качает
через yt-dlp и отдаёт готовый файл. Наружу сервер выходит через Cloudflare
Tunnel (kachalka-api.flownote.cc), сам слушает только 127.0.0.1.

Запуск: phone/run.sh (его держит launchd, см. phone/README.md).
"""
import hashlib
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

PORT = int(os.environ.get("KACHALKA_PORT", "8977"))
WORK = Path.home() / "Library" / "Caches" / "Kachalka-phone"
STATS = Path.home() / "Library" / "Application Support" / "Kachalka-phone" / "stats.json"
PUSH_KEY = STATS.parent / "push.key"                       # ключ для отправки счётчика на сайт
PUSH_URL = "https://kachalka.flownote.cc/api/phone"         # счётчики на сайте и в README
GITHUB_API = "https://api.github.com/repos/kozliq/kachalka/releases?per_page=100"
TLS = ssl.create_default_context(cafile=certifi.where())   # у Python с python.org нет своих сертификатов
ORIGINS = {"https://kachalka.flownote.cc", "http://localhost:8931", "http://localhost:8932"}   # + локальные превью сайта

QUALITIES = ("1080p", "720p", "480p", "mp3")
LONG_TRACK = 120            # секунд: дольше — на телефоне кивает кот в наушниках
MAX_PARALLEL = 2            # сколько загрузок одновременно
MAX_PER_HOUR = 30           # загрузок с одного адреса в час
MAX_BYTES = 2 * 1024 ** 3   # больше 2 ГБ не качаем
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


def friendly_error(raw):
    m = raw.lower()
    if "max_filesize" in m or "larger than max" in m:
        return "Файл больше 2 ГБ — для телефона это слишком. Выбери 720p или 480p."
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
            day = time.strftime("%Y-%m-%d")
            s["total"] = s.get("total", 0) + 1
            days = s.setdefault("days", {})
            days[day] = days.get(day, 0) + 1
            site = re.sub(r"^(www|m|vm|vt)\.", "", re.sub(r"^https?://([^/]+).*", r"\1", job.url))
            sites = s.setdefault("sites", {})
            sites[site] = sites.get(site, 0) + 1
            users = s.setdefault("users", [])     # случайные номера телефонов (не личные данные)
            if job.uid and job.uid not in users:
                users.append(job.uid)
            STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
        push_total(s["total"], len(s["users"]))
    except Exception as e:
        print("stats:", e, flush=True)


def github_counts():
    """Сколько раз скачали программу с GitHub и последняя версия.

    Cloudflare сам у GitHub спросить не может (общий лимит запросов на его адреса),
    поэтому спрашивает Мак и отправляет на сайт вместе с телефонными числами.
    """
    try:
        req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "kachalka-phone",
                                                          "Accept": "application/vnd.github+json"})
        rs = json.loads(urllib.request.urlopen(req, timeout=20, context=TLS).read())
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


class Job:
    def __init__(self, url, quality, uid=""):
        self.id = uuid.uuid4().hex[:12]
        self.uid = hashlib.sha256(uid.encode()).hexdigest()[:16] if uid else ""
        self.url = url
        self.quality = quality
        self.audio = quality == "mp3"
        self.height = 0 if self.audio else int(quality.rstrip("p"))
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
        if self.path and os.path.exists(self.path):
            d["name"] = os.path.basename(self.path)
            d["size"] = os.path.getsize(self.path)
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
            caffeinate = None
            try:   # пока качаем, Мак не засыпает
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
            parts = info.get("requested_formats") or [info]
            sizes = [p.get("filesize") or p.get("filesize_approx") or 0 for p in parts]
            total = sum(sizes)
            for p, s in zip(parts, sizes):
                self.weights[p.get("format_id")] = s / total if total else 1 / len(parts)
            self.title = (info.get("title") or "")[:120]
            self.long = self.audio and (info.get("duration") or 0) > LONG_TRACK
            self.stage = "Качаю звук" if self.audio else "Качаю видео"
            result = ydl.process_ie_result(info, download=True)
        path = None
        for rd in (result or {}).get("requested_downloads") or []:
            path = rd.get("filepath") or path
        if self.audio:
            mp3 = os.path.splitext(path)[0] + ".mp3" if path else None
            path = mp3 if mp3 and os.path.exists(mp3) else path
        else:
            self.stage = "Привожу файл в порядок…"
            tidy_mp4(path)
        if not path or not os.path.exists(path):
            raise RuntimeError("файл не получился")
        self.path = path
        self.progress = 1.0


def phone_numbers():
    try:
        st = json.loads(STATS.read_text("utf-8"))
        return st.get("total", 0), len(st.get("users", []))
    except Exception:
        return 0, 0


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
            os._exit(0)     # launchd поднимет заново, run.sh обновит yt-dlp


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
        recent = [t for t in hits.get(ip, []) if now - t < 3600]
        if len(recent) >= MAX_PER_HOUR:
            return self.json(429, {"error": "Слишком много загрузок за час. Передохни немного."})
        hits[ip] = recent + [now]
        uid = data.get("uid") if isinstance(data.get("uid"), str) else ""
        job = Job(m.group(0), quality, uid[:64])
        with jobs_lock:
            jobs[job.id] = job
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
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})(/file)?", path)
        job = jobs.get(m.group(1)) if m else None
        if not job:
            return self.json(404, {"error": "Загрузка не найдена — возможно, файл уже удалён. Скачай заново."})
        if not m.group(2):
            return self.json(200, job.public())
        if job.state != "done" or not job.path or not os.path.exists(job.path):
            return self.json(409, {"error": "Файл ещё не готов"})
        self.send_file(job, head)

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
