@echo off
rem One-click build: clean -> PyInstaller -> ShudaoLe-v<version>.zip
rem Logic lives in build.py (keep this file pure ASCII: cmd's batch parser
rem miscounts multibyte characters and desyncs mid-file, tested & confirmed).
python build.py
pause
