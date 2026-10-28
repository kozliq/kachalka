"""Собирает программу: Kachalka.exe на Windows, Kachalka.app на Mac.

Запуск: pip install -r requirements.txt pyinstaller && python build.py
"""
import hashlib
import io
import os
import platform
import re
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

import certifi
import PyInstaller.__main__
HERE = Path(__file__).resolve().parent
os.chdir(HERE)

# deno решает задачки YouTube на JavaScript — без него YouTube видео не отдаёт
DENO_VERSION = "2.9.8"


def fetch_deno():
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    target = {"win32": "pc-windows-msvc", "darwin": "apple-darwin"}.get(sys.platform, "unknown-linux-gnu")
    name = f"deno-{arch}-{target}.zip"
    url = f"https://github.com/denoland/deno/releases/download/v{DENO_VERSION}/{name}"
    ctx = ssl.create_default_context(cafile=certifi.where())
    data = urllib.request.urlopen(url, context=ctx).read()
    # у Windows-сборки файл суммы в виде таблицы PowerShell — берём из него 64 знака хеша
    want = re.search(r"\b[0-9a-fA-F]{64}\b", urllib.request.urlopen(url + ".sha256sum", context=ctx)
                     .read().decode()).group().lower()
    if hashlib.sha256(data).hexdigest() != want:
        sys.exit(f"{name}: контрольная сумма не сошлась")
    exe = "deno.exe" if sys.platform == "win32" else "deno"
    out = HERE / "build" / "deno" / exe
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(zipfile.ZipFile(io.BytesIO(data)).read(exe))
    out.chmod(0o755)
    return out


# иконку рисует make_icon.py, готовые файлы лежат в репозитории
args = [
    "main.py",
    "--name=Kachalka",
    "--windowed",
    "--noconfirm",
    "--clean",
    f"--add-data=animation.gif{os.pathsep}.",
    f"--add-data=cat.gif{os.pathsep}.",
    f"--add-data=vibe.gif{os.pathsep}.",
    f"--add-data=rick.gif{os.pathsep}.",
    "--collect-data=customtkinter",
    "--collect-submodules=yt_dlp",
    "--collect-all=imageio_ffmpeg",
    "--collect-all=curl_cffi",      # «притворяется» браузером — без этого TikTok не отдаёт видео
    "--collect-all=yt_dlp_ejs",     # скрипты для deno
    f"--add-binary={fetch_deno()}{os.pathsep}.",
]
if sys.platform == "win32":
    args += ["--onefile", "--icon=icon.ico"]
else:
    args += ["--icon=icon.icns", "--osx-bundle-identifier=com.kozliq.kachalka"]
PyInstaller.__main__.run(args)
