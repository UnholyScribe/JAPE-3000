@echo off

title JAPE 3000

where python >nul 2>nul || (echo Python 3.10+ is required. & pause & exit /b 1)

python -m pip install --quiet zstandard pywebview

python ja3_gui.py %*

