@echo off
REM TNT GROUP - TNT Downloader (chay tren Windows)
cd /d "%~dp0"
where python >nul 2>nul || (echo Chua cai Python 3.10+. & pause & exit /b 1)
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
REM KHONG can 'playwright install chromium': che do bat luong dung Microsoft Edge
REM (hoac Chrome) da cai san tren may. Chi chay lenh do neu may khong co ca hai.
python app.py
pause
