#!/bin/sh
# Запуск сервера Качалки для телефона (его держит systemd на сервере или launchd на Маке).
# Перед стартом тихо обновляет yt-dlp: сайты меняются, свежий yt-dlp — меньше ошибок.
cd "$(dirname "$0")"
VENV="${KACHALKA_VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  PY=python3
  [ -x /usr/local/bin/python3.14 ] && PY=/usr/local/bin/python3.14   # Мак: Python с python.org
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -U "yt-dlp[default,curl-cffi]" certifi >/dev/null 2>&1 &
PIP=$!
( sleep 120; kill $PIP 2>/dev/null ) &
wait $PIP 2>/dev/null
[ -d /opt/homebrew/bin ] && export PATH="/opt/homebrew/bin:$PATH"   # Мак: ffmpeg из Homebrew
exec "$VENV/bin/python" server.py
