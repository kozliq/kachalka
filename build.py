"""Собирает программу: Kachalka.exe на Windows, Kachalka.app на Mac.

Запуск: pip install -r requirements.txt pyinstaller && python build.py
"""
import os
import sys
from pathlib import Path

import PyInstaller.__main__
HERE = Path(__file__).resolve().parent
os.chdir(HERE)

# иконку рисует make_icon.py, готовые файлы лежат в репозитории
args = [
    "main.py",
    "--name=Kachalka",
    "--windowed",
    "--noconfirm",
    "--clean",
    f"--add-data=animation.gif{os.pathsep}.",
    "--collect-data=customtkinter",
    "--collect-submodules=yt_dlp",
    "--collect-all=imageio_ffmpeg",
]
if sys.platform == "win32":
    args += ["--onefile", "--icon=icon.ico"]
else:
    args += ["--icon=icon.icns", "--osx-bundle-identifier=com.kozliq.kachalka"]
PyInstaller.__main__.run(args)
