#!/usr/bin/env bash
# Dựng TNT Downloader thành .app trên macOS.
#
# Chạy:  bash build_mac.sh                  # bản gọn, dùng Chrome/Edge có sẵn trên máy
#        BUNDLE_CHROMIUM=1 bash build_mac.sh   # nhúng luôn Chromium (+~300MB), máy trắng vẫn chạy
#
# Chạy được trên máy Mac hoặc trong GitHub Actions (macos runner).
set -euo pipefail
cd "$(dirname "$0")"

BUNDLE_CHROMIUM="${BUNDLE_CHROMIUM:-0}"
APP="dist/TNT_Downloader.app"

echo "==> 1/6 Tạo venv + cài thư viện"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo "==> 2/6 Tải ffmpeg STATIC cho mac vào vendor/ffmpeg (đúng kiến trúc)"
mkdir -p vendor/ffmpeg
if [ ! -x vendor/ffmpeg/ffmpeg ]; then
  # PHẢI khớp kiến trúc runner: arm64 (Apple Silicon) hay x86_64 (Intel). Nhúng
  # ffmpeg Intel vào app arm64 thì máy Apple Silicon phải có Rosetta mới chạy.
  case "$(uname -m)" in
    arm64)  MR=arm64 ;;
    *)      MR=amd64 ;;
  esac
  echo "   Kiến trúc: $(uname -m) -> tải ffmpeg $MR"
  curl -fL -o /tmp/ffmpeg.zip "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${MR}/release/ffmpeg.zip"
  unzip -o /tmp/ffmpeg.zip -d vendor/ffmpeg
  chmod +x vendor/ffmpeg/ffmpeg
fi
file vendor/ffmpeg/ffmpeg || true

if [ "$BUNDLE_CHROMIUM" = "1" ]; then
  echo "==> 3/6 Nạp Chromium cho Playwright (sẽ nhúng vào .app)"
  python -m playwright install chromium
else
  echo "==> 3/6 BỎ QUA Chromium — app dùng Chrome/Edge cài sẵn trên máy Mac đích"
fi

echo "==> 4/6 Build .app"
rm -rf build dist
pyinstaller tnt_downloader_mac.spec

if [ "$BUNDLE_CHROMIUM" = "1" ]; then
  echo "==> 4b/6 Copy Chromium vào .app SAU pyinstaller"
  # KHÔNG để PyInstaller nhúng: trên arm64 nó codesign TỪNG file, gặp nested bundle
  # 'Chromium.app' + .framework -> 'bundle format unrecognized' -> build fail.
  MSP="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}"
  DEST="$APP/Contents/Frameworks/ms-playwright"
  mkdir -p "$DEST"
  cp -R "$MSP/." "$DEST/"
  find "$DEST" -type f \( -name 'Chromium' -o -name 'chrome_crashpad_handler' \
       -o -path '*/MacOS/*' \) -exec chmod +x {} \; 2>/dev/null || true
  # .links là thư mục hardlink-metadata -> 'codesign --deep' báo lỗi rồi BỎ KÝ cả
  # app. Playwright tự dò theo chromium-<rev> nên xoá đi cho ký sạch.
  rm -rf "$DEST/.links"
  echo "   Đã copy Chromium từ $MSP"
fi

echo "==> 5/6 Ad-hoc codesign (arm64 BẮT BUỘC có chữ ký hợp lệ mới chạy)"
chmod +x "$APP/Contents/Frameworks/ffmpeg/ffmpeg" 2>/dev/null || true
codesign --force -s - "$APP/Contents/Frameworks/ffmpeg/ffmpeg"
codesign --force --deep --sign - "$APP"
codesign --verify --strict "$APP/Contents/Frameworks/ffmpeg/ffmpeg" \
  || { echo "  *** ffmpeg CHƯA ký — DỪNG"; exit 1; }
codesign --verify "$APP" || { echo "  *** .app CHƯA ký hợp lệ — DỪNG"; exit 1; }
echo "   Chữ ký OK."

echo "==> 6/6 Đóng gói zip (ditto giữ nguyên symlink + quyền thực thi)"
( cd dist && ditto -c -k --sequesterRsrc --keepParent \
    TNT_Downloader.app TNT_Downloader-mac.zip )

du -sh "$APP" dist/TNT_Downloader-mac.zip
echo "XONG: $APP  (+ dist/TNT_Downloader-mac.zip để gửi)"
echo "Nhớ: đặt license.key CẠNH TNT_Downloader.app trên máy người dùng."
