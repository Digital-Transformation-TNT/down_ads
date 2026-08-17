#!/usr/bin/env bash
# TNT GROUP - TNT Downloader (chạy từ mã nguồn trên macOS/Linux)
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Chưa cài Python 3.10+"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
# KHÔNG cần 'playwright install chromium': chế độ bắt luồng dùng Google Chrome /
# Microsoft Edge đã cài trên máy. Chỉ chạy lệnh đó nếu máy không có cả hai.
python app.py
