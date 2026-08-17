# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — dựng TNT Downloader thành .app (macOS).

PHẢI build TRÊN macOS (PyInstaller không cross-compile). Dùng `bash build_mac.sh`
hoặc GitHub Actions (.github/workflows/build-macos.yml) — không cần sở hữu máy Mac.

Kết quả: dist/TNT_Downloader.app  (+ dist/TNT_Downloader-mac.zip để gửi)

TRÌNH DUYỆT: mặc định KHÔNG nhúng Chromium (~300 MB) — app mở Google Chrome /
Microsoft Edge có sẵn trên máy Mac. Máy nào không có cả hai thì build lại với
BUNDLE_CHROMIUM=1 (build_mac.sh sẽ copy Chromium vào .app sau khi pyinstaller chạy).

ffmpeg: bản STATIC theo đúng kiến trúc, đặt ở vendor/ffmpeg/ffmpeg (build_mac.sh tự tải).

LƯU Ý Gatekeeper: app chưa notarize sẽ bị macOS chặn khi tải từ mạng. Người dùng
cuối chỉ cần làm 1 lần:
    xattr -dr com.apple.quarantine TNT_Downloader.app
hoặc chuột phải -> Open. Muốn double-click mượt hẳn cần Apple Developer ($99).
"""
import os
import shutil

from PyInstaller.utils.hooks import collect_all

BUNDLE_FFMPEG = True
BUNDLE_FFPROBE = False      # không cần: thiếu ffprobe thì kiểm tra video bằng ffmpeg
BUNDLE_PLAYWRIGHT = True    # giữ chế độ "bắt luồng bằng trình duyệt" (video quảng cáo)
# Chromium KHÔNG nhúng qua PyInstaller: trên arm64 PyInstaller codesign TỪNG file,
# gặp nested bundle của Chromium ('Chromium.app' + .framework) -> lỗi "bundle format
# unrecognized" -> build fail. build_mac.sh copy Chromium vào .app SAU pyinstaller
# rồi 'codesign --deep' ký gọn cả bundle.

datas, binaries, hiddenimports = [], [], []

# yt_dlp nạp extractor động; curl_cffi kèm thư viện nhị phân; cryptography (lớp
# license) có phần Rust biên dịch (_rust) + cffi -> PHẢI collect_all, thiếu là app
# chạy trên Mac báo "No module named 'cryptography'".
packages = ["yt_dlp", "curl_cffi", "cryptography", "cffi"]
if BUNDLE_PLAYWRIGHT:
    packages.append("playwright")
for pkg in packages:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] *** không gom được {pkg}: {e} ***")
hiddenimports += ["cryptography.hazmat.bindings._rust", "_cffi_backend"]

if os.path.exists("logo.png"):
    datas += [("logo.png", ".")]

# --- ffmpeg: thêm vào BINARIES (không phải datas) để giữ quyền thực thi ---
if BUNDLE_FFMPEG:
    for tool in ["ffmpeg"] + (["ffprobe"] if BUNDLE_FFPROBE else []):
        src = os.path.join("vendor", "ffmpeg", tool)
        if not os.path.isfile(src):
            src = shutil.which(tool)
        if src and os.path.isfile(src):
            binaries += [(src, "ffmpeg")]
            print(f"[spec] nhúng {tool}: {src} ({os.path.getsize(src)/1048576:.0f} MB)")
        else:
            print(f"[spec] *** CẢNH BÁO: không thấy {tool} (đặt bản static vào vendor/ffmpeg/) ***")

# Thư viện KHÔNG dùng nhưng PyInstaller hay kéo theo -> loại cho nhẹ.
EXCLUDES = [
    "imageio_ffmpeg",           # ffmpeg thứ 2 — đã nhúng bản static rồi
    "numpy", "scipy", "pandas", "PIL", "matplotlib", "IPython", "lxml",
    "tkinter", "test", "pydoc_data",
    # KHÔNG loại sqlite3: yt-dlp cần nó để đọc cookies từ trình duyệt.
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtPositioning",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "tnt_license", "cryptography",
        "core.engine", "core.extractors", "core.sniffer", "core.cookies", "core.utils",
        "requests",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TNT_Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="logo.icns" if os.path.exists("logo.icns") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TNT_Downloader",
)

app = BUNDLE(
    coll,
    name="TNT_Downloader.app",
    icon="logo.icns" if os.path.exists("logo.icns") else None,
    bundle_identifier="com.tntgroup.downloader",
    info_plist={
        "CFBundleName": "TNT Downloader",
        "CFBundleDisplayName": "TNT Downloader",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
