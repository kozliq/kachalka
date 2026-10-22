# Качалка на телефоне

Телефонная версия — это сайт **kachalka.flownote.cc/app**, который ставится на экран «Домой» и открывается как приложение. Сам айфон видео с YouTube скачать не может, поэтому качает компьютер:

```
телефон ──ссылка──▶ kachalka-api.flownote.cc ──туннель Cloudflare──▶ server.py на Маке ──yt-dlp──▶ YouTube, TikTok…
телефон ◀──готовый mp4 / mp3────────────────────────────────────────────┘
```

1. Страница (`site/app/` на Cloudflare, работает всегда) отправляет ссылку на `kachalka-api.flownote.cc`.
2. Cloudflare Tunnel передаёт запрос на Мак, где `server.py` качает видео через yt-dlp — так же, как программа.
3. Страница каждую секунду спрашивает, как дела (проценты, скорость), и показывает челика или котика.
4. Готовый файл приходит на телефон. На iPhone кнопка «Сохранить в Фото» открывает системное меню «Сохранить видео».

Файлы удаляются с Мака через два часа. Пока идёт загрузка, Мак не засыпает. Если Мак выключен или спит, на странице будет «сервер спит».

## Что где

| Что | Где |
|---|---|
| Сервер | `phone/server.py` (слушает только `127.0.0.1:8977`) |
| Запуск + обновление yt-dlp | `phone/run.sh` |
| Автозапуск | `~/Library/LaunchAgents/cc.flownote.kachalka-server.plist` и `cc.flownote.kachalka-tunnel.plist` |
| Туннель | `~/.cloudflared/kachalka.yml` → `kachalka-api.flownote.cc` |
| Логи | `~/Library/Logs/Kachalka-phone/` |
| Статистика | `~/Library/Application Support/Kachalka-phone/stats.json` (видео по дням и сайтам, число разных телефонов) |
| Страница-приложение | `site/app/` — папка сайта, в репозиторий не входит (выкладка: `npx wrangler deploy` в `site/`) |
| Счётчики на сайте и в README | `site/worker.js` → `/api/downloads`, `/api/badge/phone` (ключ отправки — `push.key` рядом со статистикой и секрет `PHONE_KEY` в Cloudflare) |

## Ограничения

- Не больше 2 загрузок одновременно и 30 загрузок в час с одного адреса.
- Файл до 2 ГБ.
- Раз в сутки сервер сам перезапускается и обновляет yt-dlp (если ничего не качает).

## Команды

```bash
launchctl kickstart -k gui/$(id -u)/cc.flownote.kachalka-server   # перезапустить сервер
launchctl kickstart -k gui/$(id -u)/cc.flownote.kachalka-tunnel   # перезапустить туннель
launchctl bootout gui/$(id -u)/cc.flownote.kachalka-server        # выключить (и туннель так же)
curl -s https://kachalka-api.flownote.cc/api/ping                  # жив ли сервер снаружи
```

## Свой сервер

Хочешь поднять у себя: `python3 -m venv phone/.venv && phone/.venv/bin/pip install "yt-dlp[default,curl-cffi]"`, нужен `ffmpeg`. Запуск — `phone/run.sh`. Наружу — свой Cloudflare Tunnel. Страница — [kachalka.flownote.cc/app](https://kachalka.flownote.cc/app/), её код открыт прямо в браузере; адрес сервера в ней — константа `API`.
