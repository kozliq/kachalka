#!/bin/zsh
# Запуск сервера Качалки для телефона (его держит launchd).
# Перед стартом тихо обновляет yt-dlp: сайты меняются, свежий yt-dlp — меньше ошибок.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  /usr/local/bin/python3.14 -m venv .venv
fi
.venv/bin/pip install -q -U "yt-dlp[default,curl-cffi]" >/dev/null 2>&1 &
PIP=$!
( sleep 120; kill $PIP 2>/dev/null ) &
wait $PIP 2>/dev/null
export PATH="/opt/homebrew/bin:$PATH"
exec .venv/bin/python server.py
