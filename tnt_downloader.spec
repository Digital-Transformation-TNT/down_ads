# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller spec — dựng TNT Downloader thành .exe (Windows, one-dir).

Build (LUÔN build trong venv SẠCH — xem ghi chú "patchright" bên dưới):
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt pyinstaller
    .venv\Scripts\python -m PyInstaller tnt_downloader.spec

⚠ ĐỪNG build bằng Python hệ thống/Anaconda dùng chung: gói nào cài trong đó mà có
đăng ký hook PyInstaller sẽ tự chui vào bản phát hành. Đã dính thật với
`patchright` (bản fork của Playwright, do tool khác cài) — nó đăng ký hook cho
`playwright.sync_api` nên bơm thêm 102 MB vào gói dù tool KHÔNG hề import, và
`excludes` không chặn được vì hook thêm file trực tiếp chứ không qua import.

Kết quả:  dist/TNT_Downloader/TNT_Downloader.exe

PHÁT HÀNH: copy nguyên thư mục dist/TNT_Downloader, đặt `license.key` của MÁY ĐÓ
ngay CẠNH file .exe. Không có license.key thì app hiện mã máy rồi thoát.

──────────────────────── DUNG LƯỢNG ────────────────────────
Bản đầu tiên nặng 1.3 GB vì nhúng nguyên Chromium + 2 bộ ffmpeg. Đã cắt:

  ms-playwright (Chromium)   685 MB → 0    dùng Edge/Chrome CÀI SẴN trên máy
  ffprobe.exe                 97 MB → 0    kiểm tra video bằng chính ffmpeg
  imageio_ffmpeg              84 MB → 0    trùng với ffmpeg.exe đã nhúng
  numpy / PIL / tkinter…      ~35 MB → 0    không dùng tới

Còn lại ~330 MB: ffmpeg 97 · playwright (node driver) 105 · PySide6 93 · Python + yt-dlp.
Muốn nhẹ nữa thì tắt BUNDLE_PLAYWRIGHT (−105 MB, mất đường tải video quảng cáo
bằng trình duyệt) hoặc BUNDLE_FFMPEG (−97 MB, máy đích phải tự có ffmpeg).
"""
import os
import shutil

from PyInstaller.utils.hooks import collect_all

BUNDLE_FFMPEG = True        # nhúng ffmpeg.exe (ghép slideshow, tải HLS, kiểm tra video)
BUNDLE_FFPROBE = False      # thường KHÔNG cần: thiếu ffprobe thì dùng ffmpeg thay
BUNDLE_PLAYWRIGHT = True    # giữ chế độ "bắt luồng bằng trình duyệt" (video quảng cáo)
BUNDLE_CHROMIUM = False     # ⚠ 685 MB. Chỉ bật khi máy đích không có Edge/Chrome

datas, binaries, hiddenimports = [], [], []

# yt_dlp nạp extractor động + curl_cffi kèm thư viện nhị phân -> phải gom đầy đủ,
# thiếu là exe chạy được nhưng tải link nào cũng lỗi "no extractor".
packages = ["yt_dlp", "curl_cffi"] + (["playwright"] if BUNDLE_PLAYWRIGHT else [])
for pkg in packages:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] *** không gom được {pkg}: {e} ***")

if os.path.exists("logo.png"):
    datas += [("logo.png", ".")]

# --- ffmpeg -> thư mục con ffmpeg/ (core.utils._bundled tìm ở đây) ---
if BUNDLE_FFMPEG:
    tools = ["ffmpeg"] + (["ffprobe"] if BUNDLE_FFPROBE else [])
    for tool in tools:
        src = os.path.join("vendor", "ffmpeg", tool + ".exe")   # ưu tiên bản trong project
        if not os.path.isfile(src):
            src = shutil.which(tool)
        if src and os.path.isfile(src):
            datas += [(src, "ffmpeg")]
            print(f"[spec] nhúng {tool}: {src} ({os.path.getsize(src)/1048576:.0f} MB)")
        else:
            print(f"[spec] *** CẢNH BÁO: không thấy {tool} để nhúng (đặt vào vendor/ffmpeg/) ***")

# --- Chromium: chỉ khi thật sự cần (máy đích trắng, không có Edge lẫn Chrome) ---
if BUNDLE_CHROMIUM:
    msp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not msp or not os.path.isdir(msp):
        msp = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                           "ms-playwright")
    if os.path.isdir(msp):
        n = 0
        for root, _dirs, files in os.walk(msp):
            # Bỏ headless_shell (269 MB): app mở trình duyệt có giao diện để người
            # dùng đăng nhập/bấm play, không chạy headless shell.
            if "headless_shell" in root:
                continue
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(os.path.dirname(full), msp)
                dest = "ms-playwright" if rel == "." else os.path.join("ms-playwright", rel)
                datas += [(full, dest)]
                n += 1
        print(f"[spec] nhúng Chromium từ {msp} ({n} file)")
    else:
        print("[spec] *** CẢNH BÁO: không thấy ms-playwright — "
              "chạy 'python -m playwright install chromium' trước ***")

# Thư viện KHÔNG dùng nhưng PyInstaller hay kéo theo (nhất là khi build trong môi
# trường Anaconda). Loại thẳng cho nhẹ ~120 MB.
EXCLUDES = [
    "imageio_ffmpeg",           # ffmpeg thứ 2 (84 MB) — đã nhúng ffmpeg.exe rồi
    # patchright là bản fork của Playwright; nếu máy build có cài nó (tool khác
    # dùng) thì nó tự đăng ký hook PyInstaller cho playwright và kéo thêm 102 MB
    # vào gói dù tool KHÔNG hề import. Loại thẳng.
    "patchright",
    "numpy", "scipy", "pandas", "PIL", "matplotlib", "IPython", "lxml",
    "tkinter", "test", "pydoc_data",
    # KHÔNG loại sqlite3: yt-dlp cần nó để đọc cookies từ trình duyệt.
    # Qt: tool chỉ dùng QtCore/QtGui/QtWidgets
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "shiboken6.Qt3DCore",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "tnt_license", "cryptography",              # lớp bảo mật license
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
    console=False,                       # app GUI -> không bật cửa sổ console
    icon="logo.ico" if os.path.exists("logo.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TNT_Downloader",
)
