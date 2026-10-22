# Качалка на телефоне

Телефонная версия — это сайт **kachalka.flownote.cc/app**, который ставится на экран «Домой» и открывается как приложение. Сам айфон видео с YouTube скачать не может, поэтому качает сервер:

```
телефон ──ссылка──▶ kachalka-api.flownote.cc ──туннель Cloudflare──▶ server.py на сервере ──yt-dlp──▶ YouTube, TikTok…
телефон ◀──готовый mp4 / mp3──────────────────────────────────────────────┘
```

1. Страница (`site/app/` на Cloudflare, работает всегда) отправляет ссылку на `kachalka-api.flownote.cc`.
2. Cloudflare Tunnel передаёт запрос на сервер в Финляндии, где `server.py` качает видео через yt-dlp — так же, как программа.
3. Страница каждую секунду спрашивает, как дела (проценты, скорость), и показывает челика или котика.
4. Готовый файл приходит на телефон. Кнопка «Сохранить в Фото» открывает системное меню «Сохранить видео», «Отправить» — то же меню с Telegram, WhatsApp и остальными.

Сервер работает круглые сутки. Файлы удаляются через два часа (или раньше, если на диске мало места).

## Что где

| Что | Где |
|---|---|
| Сервер | `phone/server.py` (слушает только `127.0.0.1:8977`), на сервере — `/opt/kachalka-phone/` |
| Запуск + обновление yt-dlp | `phone/run.sh` |
| Автозапуск | systemd: `kachalka-phone.service` (сервер, отдельный пользователь `kachalka`) и `kachalka-tunnel.service` (туннель) |
| Туннель | `/etc/cloudflared/kachalka.yml` → `kachalka-api.flownote.cc` |
| Логи | `journalctl -u kachalka-phone` и `journalctl -u kachalka-tunnel` |
| Статистика | `/var/lib/kachalka-phone/stats.json` (видео по дням и сайтам, число разных телефонов) |
| Страница-приложение | `site/app/` — папка сайта, в репозиторий не входит (выкладка: `npx wrangler deploy` в `site/`) |
| Счётчики на сайте и в README | `site/worker.js` → `/api/downloads`, `/api/badge/phone` (ключ отправки — `push.key` рядом со статистикой и секрет `PHONE_KEY` в Cloudflare) |

Папки и лимиты задаются в службе переменными: `KACHALKA_WORK` (временные файлы), `KACHALKA_DATA` (статистика и ключ), `KACHALKA_PARALLEL`, `KACHALKA_MAX_GB`. Без них — папки Мака и лимиты по умолчанию.

## Ограничения

- На сервере одна загрузка за раз (по умолчанию две) и 30 загрузок в час с одного адреса.
- Файл до 1,5 ГБ (по умолчанию 2 ГБ).
- Качалка уступает VPN, который живёт на том же сервере: ниже приоритет процессора и диска, не больше 550 МБ памяти.
- Раз в сутки сервер сам перезапускается и обновляет yt-dlp (если ничего не качает).

## Команды (на сервере)

```bash
systemctl restart kachalka-phone       # перезапустить сервер (и обновить yt-dlp)
systemctl restart kachalka-tunnel      # перезапустить туннель
journalctl -u kachalka-phone -n 50     # что пишет сервер
curl -s https://kachalka-api.flownote.cc/api/ping   # жив ли сервер снаружи
```

Запасной вариант — Мак: `phone/run.sh` под launchd (`~/Library/LaunchAgents/cc.flownote.kachalka-*.plist`, сейчас выключены). Одновременно туннель должен работать только в одном месте, иначе Cloudflare делит запросы пополам.

## Свой сервер

Хочешь поднять у себя: `python3 -m venv phone/.venv && phone/.venv/bin/pip install "yt-dlp[default,curl-cffi]"`, нужен `ffmpeg`. Запуск — `phone/run.sh`. Наружу — свой Cloudflare Tunnel. Страница — [kachalka.flownote.cc/app](https://kachalka.flownote.cc/app/), её код открыт прямо в браузере; адрес сервера в ней — константа `API`.
