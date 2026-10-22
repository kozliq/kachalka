"""Качалка в Telegram: бот.

Человек кидает боту ссылку — бот отдаёт её серверу Качалки (server.py рядом,
127.0.0.1:8977), показывает, как идёт загрузка, и присылает готовое видео или
MP3 прямо в чат. Telegram разрешает ботам отправлять файлы до 50 МБ, поэтому
сервер подбирает качество, которое влезает; не влезло — бот даёт ссылку.

Запуск: .venv/bin/python bot.py (на сервере — служба kachalka-bot).
Токен бота — файл bot.token рядом со статистикой (KACHALKA_DATA).
Аватарка («К», как у программы) ставится один раз: .venv/bin/python bot.py avatar.
Только стандартная библиотека Python (+ certifi, если есть).
"""
import html
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path

try:
    import certifi
    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

ENV = os.environ.get
HERE = Path(__file__).resolve().parent
DATA = Path(ENV("KACHALKA_DATA") or Path.home() / "Library" / "Application Support" / "Kachalka-phone")
TG_API = ENV("KACHALKA_TG_API", "https://api.telegram.org")
LOCAL = ENV("KACHALKA_LOCAL") or f"http://127.0.0.1:{ENV('KACHALKA_PORT', '8977')}"
PUBLIC = "https://kachalka-api.flownote.cc"     # отсюда большие файлы качают по ссылке
SITE = "https://kachalka.flownote.cc"

TG_MAX = 50 * 1024 * 1024       # больше Telegram от ботов не принимает
FIT = 47 * 1024 * 1024          # под это сервер подбирает качество (размер заранее известен примерно)
UPLOADS = threading.Semaphore(2)    # файлы держим в памяти, поэтому отправляем не больше двух сразу

# Лимиты. Бот открыт всем, а сервер один и качает по одному видео за раз (канал делит с VPN):
# ни один человек и ни толпа аккаунтов не должны забить очередь остальным.
AT_ONCE = 3                 # ссылок можно кинуть разом…
PACE = 20                   # …дальше — по одной раз в 20 секунд
PER_HOUR, PER_DAY = 25, 100     # загрузок на человека
DAY_BYTES = 3 * 1024 ** 3       # и не больше 3 ГБ файлов за сутки
LINE_PRIVATE, LINE_GROUP = 3, 5     # ссылок в очереди одного чата (качаются по одной)
QUEUE_MAX = 15              # загрузок из бота всего (качаются + ждут); больше — «приходи позже»
FLOOD = 20                  # сообщений за минуту — дальше это спам…
MUTES = {600: "10 минут", 3600: "час", 86400: "сутки"}     # …и бот молчит: 10 минут, потом час, потом сутки
HANDLERS = threading.BoundedSemaphore(50)   # сообщений в работе сразу; лишние во время атаки отбрасываем

URL_RE = re.compile(r"https?://[^\s<>\"']+")
AUDIO_WORDS = re.compile(r"\b(mp3|мп3|звук|музык\w*|аудио|audio|песн\w*|трек)\b", re.I)
# в группах качаем только ссылки с видеосайтов, чтобы не отвечать на каждую новость
VIDEO_SITES = re.compile(
    r"https?://([\w-]+\.)*(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|vk\.com|vkvideo\.ru|"
    r"x\.com|twitter\.com|reddit\.com|redd\.it|pinterest\.\w+|pin\.it|rutube\.ru|dzen\.ru|ok\.ru|"
    r"twitch\.tv|vimeo\.com|facebook\.com|fb\.watch|soundcloud\.com|likee\.video|threads\.(net|com)|"
    r"bilibili\.com|dailymotion\.com|coub\.com)/", re.I)

# челик бежит с папкой (видео) и котик в наушниках (MP3) — те же, что в программе
ANIMATIONS = {False: HERE / "bot-video.mp4", True: HERE / "bot-audio.mp4"}
AVATAR = HERE / "bot-avatar.jpg"      # делает make_app_icon.py

HELLO = (
    "<b>Привет! Кидай ссылку на видео — пришлю его прямо сюда.</b>\n\n"
    "YouTube, TikTok, Instagram, VK, X и ещё сотни сайтов. "
    "Нужен только звук — допиши к ссылке «mp3».\n\n"
    "Бесплатно, без рекламы и водяных знаков. Ещё Качалка есть для компьютера и телефона: "
    f'<a href="{SITE}">{SITE.removeprefix("https://")}</a>'
)
NO_LINK = "Пришли ссылку на видео — например, из TikTok или YouTube. Нажми «Поделиться» → «Копировать ссылку» и вставь сюда."
SHORT_ABOUT = "Качаю видео и музыку по ссылке: YouTube, TikTok, Instagram, VK. Бесплатно и без рекламы."
ABOUT = (
    "Кидай ссылку — пришлю видео или MP3 прямо в чат.\n\n"
    "YouTube, TikTok, Instagram, VK, X и ещё сотни сайтов. "
    "Без рекламы, водяных знаков и регистрации.\n\n"
    "Открытый код: github.com/kozliq/kachalka"
)

bot_name = ""               # имя бота без @, узнаём при запуске
lock = threading.Lock()
lines = {}                  # чат → очередь его ссылок (первая качается, остальные ждут)
people = {}                 # id в Telegram → Person
job_urls = {}               # id загрузки → ссылка (для кнопки «Только звук»)
anim_ids = {}               # картинка → её file_id в Telegram, чтобы не загружать каждый раз


class TgError(Exception):
    def __init__(self, text, code=0):
        super().__init__(text)
        self.code = code


def read_token():
    token = ENV("KACHALKA_BOT_TOKEN")
    if not token:
        try:
            token = (DATA / "bot.token").read_text("utf-8").strip()
        except OSError:
            pass
    if not token:
        sys.exit(f"Нет токена бота: положи его в {DATA / 'bot.token'}")
    return token


TOKEN = read_token()


def multipart(fields, files):
    """Тело запроса с файлом. Файл не склеиваем с остальным — лишние 50 МБ памяти ни к чему."""
    boundary = uuid.uuid4().hex
    chunks = []
    for k, v in fields.items():
        if v is None:
            continue
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data, ctype) in files.items():
        safe = name.replace('"', "'").replace("\r", " ").replace("\n", " ")
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{safe}"\r\n'
                      f"Content-Type: {ctype}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return chunks, f"multipart/form-data; boundary={boundary}"


def tg(method, files=None, timeout=30, tries=3, **params):
    """Вызов Telegram Bot API. Сам ждёт, если Telegram просит притормозить."""
    params = {k: v for k, v in params.items() if v is not None}
    if files:
        body, ctype = multipart(params, files)
        headers = {"Content-Type": ctype, "Content-Length": str(sum(len(c) for c in body))}
    else:
        body = json.dumps(params, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
    for attempt in range(tries):
        req = urllib.request.Request(f"{TG_API}/bot{TOKEN}/{method}", data=body, headers=headers)
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=timeout, context=TLS).read())
        except urllib.error.HTTPError as e:
            try:
                r = json.loads(e.read() or b"{}")
            except ValueError:
                r = {}
            r.setdefault("error_code", e.code)
            r.setdefault("description", str(e))
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            if attempt == tries - 1:
                raise TgError(str(e))
            time.sleep(2 + attempt * 3)
            continue
        if r.get("ok"):
            return r.get("result")
        wait = (r.get("parameters") or {}).get("retry_after")
        if wait and attempt < tries - 1:
            time.sleep(min(wait, 60) + 1)
            continue
        raise TgError(r.get("description", "ошибка Telegram"), r.get("error_code", 0))
    raise TgError("Telegram не ответил")


def quiet(method, **params):
    """Вызов, ошибка которого ничего не ломает (удалить сообщение, ответить на нажатие кнопки)."""
    try:
        return tg(method, tries=1, **params)
    except TgError:
        return None


def local(path, data=None, timeout=20):
    """Запрос к серверу Качалки на этом же компьютере."""
    req = urllib.request.Request(LOCAL + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg or "Сервер Качалки ответил ошибкой. Попробуй ещё раз.")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise RuntimeError("Сервер Качалки сейчас перезапускается. Пришли ссылку ещё раз через минуту.")


def mb(n):
    return f"{n / 1024 / 1024:.0f}" if n >= 10 * 1024 * 1024 else f"{n / 1024 / 1024:.1f}".replace(".", ",")


def progress_text(job):
    stage = job.get("stage") or "Качаю…"
    if job.get("state") == "working" and stage.startswith("Качаю"):
        p = int((job.get("progress") or 0) * 100)
        stage = f"{stage}  {'▰' * (p // 10)}{'▱' * (10 - p // 10)}  {p}%"
    title = clean_title(job.get("title"))
    return f"{stage}\n<b>{html.escape(title[:200])}</b>" if title else stage


def clean_title(title):
    """Название без хэштегов TikTok: «Котик спит #fyp #cat» → «Котик спит»."""
    return re.sub(r"\s*#\S+", "", title or "").strip() or (title or "").strip()


def links_in(msg):
    text = msg.get("text") or msg.get("caption") or ""
    urls = URL_RE.findall(text)
    for e in msg.get("entities") or msg.get("caption_entities") or []:
        if e.get("type") == "text_link" and e.get("url"):
            urls.append(e["url"])
    return text, urls


def reply_to(message_id):
    return {"message_id": message_id, "allow_sending_without_reply": True}


def send_animation(chat, audio, caption, **params):
    """Живая картинка с подписью. Файл грузится один раз, дальше — по file_id."""
    params.update(chat_id=chat, caption=caption, parse_mode="HTML", width=960, height=480)
    if anim_ids.get(audio):
        try:
            return tg("sendAnimation", animation=anim_ids[audio], **params)
        except TgError:
            anim_ids.pop(audio, None)
    path = ANIMATIONS[audio]
    msg = tg("sendAnimation", files={"animation": (path.name, path.read_bytes(), "video/mp4")}, **params)
    anim_ids[audio] = (msg.get("animation") or msg.get("document") or {}).get("file_id")
    return msg


class Status:
    """Сообщение «как дела»: бежит челик (или кивает котик), в подписи — прогресс.

    Картинку отправить не вышло — обычный текст, работает так же.
    """
    def __init__(self, chat, audio, text, reply_id):
        self.chat, self.text = chat, text
        common = {"reply_parameters": reply_to(reply_id), "disable_notification": True}
        try:
            self.id, self.anim = send_animation(chat, audio, text, **common)["message_id"], True
        except (TgError, OSError):
            self.id, self.anim = tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                                    **common)["message_id"], False

    def set(self, text):
        if text == self.text:
            return
        self.text = text
        if self.anim:
            quiet("editMessageCaption", chat_id=self.chat, message_id=self.id, caption=text, parse_mode="HTML")
        else:
            quiet("editMessageText", chat_id=self.chat, message_id=self.id, text=text, parse_mode="HTML")

    def drop(self):
        quiet("deleteMessage", chat_id=self.chat, message_id=self.id)


class Person:
    """Что человек делал за последние сутки — для лимитов. Живёт только в памяти."""
    def __init__(self):
        self.tokens, self.refilled = float(AT_ONCE), time.time()    # «ведро» ссылок: полное — AT_ONCE
        self.starts = deque()       # когда ставил загрузки
        self.sizes = deque()        # (когда, сколько байт скачал)
        self.msgs = deque()         # когда писал боту (за минуту)
        self.muted_until = 0.0
        self.strikes, self.struck = 0, 0.0      # сколько раз уже молчали и когда последний
        self.told = {}              # о чём и когда последний раз предупреждали
        self.seen = time.time()


def person(user):
    """Только под lock."""
    p = people.get(user)
    if not p:
        p = people[user] = Person()
    p.seen = time.time()
    return p


def wait_text(sec):
    sec = max(int(sec) + 1, 1)
    if sec < 60:
        return f"{sec} сек"
    return f"{-(-sec // 60)} мин" if sec < 3600 else f"{-(-sec // 3600)} ч"


def flood(user):
    """Считает сообщения человека: 0 — всё хорошо, -1 — бот на него молчит,
    N > 0 — только что замолчал на N секунд (об этом надо сказать)."""
    now = time.time()
    with lock:
        p = person(user)
        if now < p.muted_until:
            return -1
        p.msgs.append(now)
        while now - p.msgs[0] > 60:
            p.msgs.popleft()
        if len(p.msgs) <= FLOOD:
            return 0
        if now - p.struck > 86400:      # сутки вёл себя хорошо — начинаем с 10 минут
            p.strikes = 0
        sec = list(MUTES)[min(p.strikes, len(MUTES) - 1)]
        p.muted_until, p.strikes, p.struck = now + sec, p.strikes + 1, now
        p.msgs.clear()
        return sec


def fresh(user, kind, every):
    """Не говорили человеку про kind последние every секунд? Тогда скажем (и запомним)."""
    now = time.time()
    with lock:
        p = person(user)
        if now - p.told.get(kind, 0) < every:
            return False
        p.told[kind] = now
        return True


def why_not(p, chat, cap, url, audio, now):
    """Почему эту ссылку сейчас нельзя (None — можно). Только под lock."""
    while p.starts and now - p.starts[0] > 86400:
        p.starts.popleft()
    while p.sizes and now - p.sizes[0][0] > 86400:
        p.sizes.popleft()
    line = lines.get(chat, ())
    if any(t[3] == url and t[4] == audio for t in line):
        return "Эту ссылку уже качаю — подожди немного."
    if sum(map(len, lines.values())) >= QUEUE_MAX:
        return "Сейчас очень много желающих, очередь забита. Пришли ссылку через пару минут."
    if len(p.starts) >= PER_DAY:
        return f"Лимит — {PER_DAY} загрузок в сутки. Следующая — через {wait_text(p.starts[0] + 86400 - now)}."
    if sum(b for _, b in p.sizes) >= DAY_BYTES:
        return (f"Лимит — {DAY_BYTES // 1024 ** 3} ГБ файлов в сутки, он закончился. "
                f"Следующая загрузка — через {wait_text(p.sizes[0][0] + 86400 - now)}.")
    hour = [t for t in p.starts if now - t < 3600]
    if len(hour) >= PER_HOUR:
        return f"Лимит — {PER_HOUR} загрузок в час. Следующая — через {wait_text(hour[0] + 3600 - now)}."
    if len(line) >= cap:
        return "Погоди, сначала докачаю прошлые ссылки — эту пришли потом."
    p.tokens = min(AT_ONCE, p.tokens + (now - p.refilled) / PACE)
    p.refilled = now
    if p.tokens < 1:
        return f"Не так быстро: следующую ссылку можно через {wait_text((1 - p.tokens) * PACE)}."
    return None


def enqueue(chat, private, reply_id, url, audio, user, react=True):
    """Ставит ссылку в очередь чата: там качается по одной, чтобы один чат не занял весь сервер.

    Вернёт, почему нельзя (текст для человека), или None.
    """
    now = time.time()
    with lock:
        p = person(user)
        why = why_not(p, chat, LINE_PRIVATE if private else LINE_GROUP, url, audio, now)
        if why:
            return why
        p.tokens -= 1
        p.starts.append(now)
        line = lines.setdefault(chat, deque())
        line.append((chat, private, reply_id, url, audio, user))
        first = len(line) == 1
    if first:
        threading.Thread(target=run_line, args=(chat,), daemon=True).start()
    elif react:     # ждёт очереди — «глазки» на сообщении, чтобы было видно, что бот её заметил
        quiet("setMessageReaction", chat_id=chat, message_id=reply_id, reaction=[{"type": "emoji", "emoji": "👀"}])
    return None


def run_line(chat):
    while True:
        with lock:
            task = lines[chat][0]
        try:
            download(*task)
        except Exception as e:
            print("download:", type(e).__name__, e, flush=True)
        with lock:
            lines[chat].popleft()
            if not lines[chat]:
                del lines[chat]
                return


def spent(user, size):
    with lock:
        person(user).sizes.append((time.time(), size))


def janitor():
    """Забывает тех, кто сутки не писал (и не сидит в молчанке)."""
    while True:
        time.sleep(600)
        now = time.time()
        with lock:
            for user, p in list(people.items()):
                if now - p.seen > 86400 and now > p.muted_until:
                    del people[user]


def download(chat, private, reply_id, url, audio, user):
    """Одна ссылка: загрузка на сервере → прогресс в сообщении → файл в чат."""
    status = None
    try:
        status = Status(chat, audio, "Ищу видео по ссылке…", reply_id)
        job = local("/api/jobs", {"url": url, "quality": "mp3" if audio else "1080p",
                                  "fit": FIT, "from": "tg", "uid": f"tg{user}"})
        shown_at = 0.0
        while job.get("state") in ("queued", "working"):
            time.sleep(1.5)
            job = local(f"/api/jobs/{job['id']}")
            text = progress_text(job)
            if job.get("state") in ("queued", "working") and text != status.text and time.time() - shown_at >= 3:
                status.set(text)
                shown_at = time.time()
        if job.get("state") != "done":
            raise RuntimeError(job.get("error") or "Не получилось скачать. Попробуй ещё раз.")
        spent(user, job.get("size") or 0)
        send_file(chat, status, reply_id, url, job)
    except (RuntimeError, TgError) as e:
        if status:
            status.drop()
        if private:     # в группе не мусорим
            quiet("sendMessage", chat_id=chat, text=f"Не вышло. {e}", reply_parameters=reply_to(reply_id))


def send_file(chat, status, reply_id, url, job):
    audio, size = job.get("audio"), job.get("size") or 0
    if size > TG_MAX:
        return give_link(chat, status, reply_id, job)
    status.set(progress_text({**job, "state": "sending", "stage": "Отправляю…"}))
    quiet("sendChatAction", chat_id=chat, action="upload_document" if audio else "upload_video")
    with UPLOADS:
        req = urllib.request.Request(f"{LOCAL}/api/jobs/{job['id']}/file")
        try:
            data = urllib.request.urlopen(req, timeout=120).read()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise RuntimeError("Файл потерялся на сервере. Пришли ссылку ещё раз.")
        name = job.get("name") or ("Качалка.mp3" if audio else "Качалка.mp4")
        sign = f"@{bot_name}" if bot_name else None     # перешлют другу — он увидит, откуда видео
        try:
            if audio:       # название, автора и обложку Telegram берёт из самого MP3
                tg("sendAudio", files={"audio": (name, data, "audio/mpeg")}, timeout=300,
                   chat_id=chat, caption=sign, duration=job.get("duration"),
                   reply_parameters=reply_to(reply_id))
            else:
                job_urls[job["id"]] = url
                while len(job_urls) > 5000:
                    job_urls.pop(next(iter(job_urls)))
                tg("sendVideo", files={"video": (name, data, "video/mp4")}, timeout=300,
                   chat_id=chat, caption=sign, supports_streaming=True, width=job.get("width"),
                   height=job.get("height"), duration=job.get("duration"),
                   reply_parameters=reply_to(reply_id),
                   reply_markup={"inline_keyboard": [[
                       {"text": "♪ Только звук — MP3", "callback_data": f"mp3:{job['id']}"}]]})
        except TgError as e:
            if e.code == 413:           # Telegram посчитал файл слишком большим
                return give_link(chat, status, reply_id, job)
            raise RuntimeError("Telegram не принял файл. Попробуй ещё раз.")
        finally:
            del data
    status.drop()


def give_link(chat, status, reply_id, job):
    what = "Звук весит" if job.get("audio") else "Видео весит"
    size = mb(job.get("size") or 0)
    text = (f"{what} {size} МБ, а Telegram даёт ботам отправлять только до 50. "
            "Забери по ссылке — она работает два часа.")
    status.drop()
    tg("sendMessage", chat_id=chat, text=text, reply_parameters=reply_to(reply_id),
       reply_markup={"inline_keyboard": [[
           {"text": f"Скачать — {size} МБ", "url": f"{PUBLIC}/api/jobs/{job['id']}/file"}]]})


def on_message(msg):
    chat, private = msg["chat"]["id"], msg["chat"].get("type") == "private"
    user = (msg.get("from") or {}).get("id", chat)
    text, urls = links_in(msg)
    cmd, _, to = (text.split() or [""])[0].lower().partition("@")
    if cmd in ("/start", "/help"):
        if (private or to == bot_name.lower()) and fresh(user, "start", 30):
            try:
                send_animation(chat, False, HELLO)
            except (TgError, OSError):
                tg("sendMessage", chat_id=chat, text=HELLO, parse_mode="HTML",
                   link_preview_options={"is_disabled": True})
        return
    if not private:
        urls = [u for u in urls if VIDEO_SITES.match(u)]
    if not urls:
        if private and fresh(user, "no-link", 60):
            tg("sendMessage", chat_id=chat, text=NO_LINK, reply_parameters=reply_to(msg["message_id"]))
        return
    audio = bool(AUDIO_WORDS.search(URL_RE.sub(" ", text)))
    react = True
    for url in list(dict.fromkeys(urls))[:AT_ONCE]:
        why = enqueue(chat, private, msg["message_id"], url, audio, user, react)
        if why:
            if private and fresh(user, "limit", 15):    # в группе не мусорим, спамеру не отвечаем на каждое
                quiet("sendMessage", chat_id=chat, text=why, reply_parameters=reply_to(msg["message_id"]))
            break
        react = False


def on_button(q):
    msg = q.get("message") or {}
    kind, _, job_id = (q.get("data") or "").partition(":")
    url = job_urls.get(job_id)
    if not url:     # бот перезапускался — ссылку берём из сообщения, на которое ответили видео
        _, urls = links_in(msg.get("reply_to_message") or {})
        url = urls[0] if urls else None
    if kind != "mp3" or not msg or not url:
        quiet("answerCallbackQuery", callback_query_id=q["id"], text="Пришли ссылку ещё раз — эта уже устарела.")
        return
    chat = msg["chat"]
    why = enqueue(chat["id"], chat.get("type") == "private", msg["message_id"], url, True, q["from"]["id"],
                  react=False)
    quiet("answerCallbackQuery", callback_query_id=q["id"], text=why or "Качаю звук", show_alert=bool(why))


def sender(update):
    """Кто прислал апдейт — для счёта сообщений. None — бот его и так пропустит
    (болтовня в группе), такой не считаем и не разбираем."""
    if "callback_query" in update:
        return update["callback_query"]["from"]["id"]
    msg = update.get("message")
    if not msg:
        return None
    user = (msg.get("from") or {}).get("id") or msg["chat"]["id"]
    if msg["chat"].get("type") == "private":
        return user
    text, urls = links_in(msg)
    return user if text.startswith("/") or any(VIDEO_SITES.match(u) for u in urls) else None


def tell_muted(update, sec):
    text = (f"Слишком много сообщений подряд — похоже на спам. Бот не отвечает тебе {MUTES[sec]}."
            + ("" if sec == max(MUTES) else " Повторится — замолчит дольше."))
    if "callback_query" in update:
        quiet("answerCallbackQuery", callback_query_id=update["callback_query"]["id"], text=text, show_alert=True)
    elif update["message"]["chat"].get("type") == "private":
        quiet("sendMessage", chat_id=update["message"]["chat"]["id"], text=text)


def handle(update, sec=0):
    try:
        if sec:
            tell_muted(update, sec)
        elif "message" in update:
            on_message(update["message"])
        elif "callback_query" in update:
            on_button(update["callback_query"])
    except Exception as e:
        print("update:", type(e).__name__, e, flush=True)
    finally:
        HANDLERS.release()


def setup():
    """Имя, описание и меню бота — то, что человек видит до первого сообщения."""
    me = tg("getMe")

    def texts():    # Telegram отвечает на это медленно (бывает 40 с) — не держим из-за них приём сообщений
        for method, params in (
                ("setMyCommands", {"commands": [{"command": "start", "description": "Как пользоваться"}]}),
                ("setMyShortDescription", {"short_description": SHORT_ABOUT}),
                ("setMyDescription", {"description": ABOUT})):
            quiet(method, **params)
    threading.Thread(target=texts, daemon=True).start()
    return me.get("username")


def main():
    global bot_name
    while True:
        try:
            bot_name = setup()
            break
        except TgError as e:
            if e.code == 401:
                sys.exit("Telegram не принял токен — проверь bot.token")
            print("Telegram недоступен:", e, flush=True)
            time.sleep(15)
    print(f"Бот @{bot_name} запущен, сервер Качалки: {LOCAL}", flush=True)
    threading.Thread(target=janitor, daemon=True).start()
    offset = 0
    while True:
        try:
            updates = tg_updates(offset)
        except TgError as e:
            if e.code == 401:
                sys.exit("Telegram не принял токен — проверь bot.token")
            print("getUpdates:", e, flush=True)
            time.sleep(5)
            continue
        for u in updates or []:
            offset = max(offset, u["update_id"] + 1)
            # спамера отсекаем тут же, без потоков и запросов к Telegram
            user = sender(u)
            if user is None:
                continue
            sec = flood(user)
            if sec < 0 or not HANDLERS.acquire(blocking=False):
                continue
            threading.Thread(target=handle, args=(u, sec), daemon=True).start()


def tg_updates(offset):
    """Долгий опрос: Telegram держит запрос до 50 секунд, пока не появится сообщение."""
    body = json.dumps({"offset": offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]})
    req = urllib.request.Request(f"{TG_API}/bot{TOKEN}/getUpdates", data=body.encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=70, context=TLS).read())
    except urllib.error.HTTPError as e:
        raise TgError(f"HTTP {e.code}", e.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise TgError(str(e))
    if not r.get("ok"):
        raise TgError(r.get("description", ""), r.get("error_code", 0))
    return r["result"]


def set_avatar():
    """Аватарка «К». Telegram копит аватарки, поэтому старую сначала убираем."""
    try:
        tg("removeMyProfilePhoto", tries=1)
    except TgError:
        pass                            # аватарки ещё не было
    tg("setMyProfilePhoto", files={"avatar": (AVATAR.name, AVATAR.read_bytes(), "image/jpeg")},
       photo={"type": "static", "photo": "attach://avatar"})
    print("Аватарка поставлена")


if __name__ == "__main__":
    set_avatar() if sys.argv[1:] == ["avatar"] else main()
