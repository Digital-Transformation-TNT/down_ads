"""Tiện ích dùng chung: ffmpeg/ffprobe, kiểm tra file video, cấu hình, tên file.

Bản LOCAL: chạy trên máy người dùng (IP dân dụng) nên KHÔNG cần relay/proxy như
bản web trên server. Mọi request đi thẳng.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

APP_NAME = "tnt_downloader"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
# Profile Chromium riêng cho chế độ "mở trình duyệt" (đăng nhập 1 lần, dùng mãi).
BROWSER_PROFILE_DIR = os.path.join(CONFIG_DIR, "browser_profile")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")

# Header trình duyệt — nhiều site (Douyin/TikTok) chặn request "trần".
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
             "Mobile/15E148 Safari/604.1")

# Cờ ẩn cửa sổ console khi gọi ffmpeg/ffprobe từ app GUI trên Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ───────────────────────────── ffmpeg ─────────────────────────────
_FFMPEG = None
_FFPROBE = None


def _bundled(name: str) -> str:
    """Tìm ffmpeg/ffprobe ĐƯỢC ĐÓNG GÓI trong bản .exe (máy đích không cần cài gì)."""
    bases = []
    mp = getattr(sys, "_MEIPASS", None)          # PyInstaller: thư mục giải nén tài nguyên
    if mp:
        bases.append(mp)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        bases += [exe_dir, os.path.join(exe_dir, "_internal")]
    fname = name + (".exe" if os.name == "nt" else "")
    for b in bases:
        for cand in (os.path.join(b, "ffmpeg", fname), os.path.join(b, fname)):
            if os.path.isfile(cand):
                if os.name != "nt":
                    # macOS/Linux: PyInstaller có thể làm mất bit +x -> cấp lại,
                    # không thì gọi ffmpeg sẽ dính "Permission denied".
                    try:
                        os.chmod(cand, os.stat(cand).st_mode | 0o111)
                    except OSError:
                        pass
                return cand
    return ""


def launch_persistent(pw, profile_dir: str, headless: bool = False, **kw):
    """Mở Chromium hồ sơ bền, ƯU TIÊN trình duyệt CÀI SẴN trên máy.

    Máy Windows nào cũng có Edge, nhiều máy có Chrome — dùng luôn binary đó
    (`channel=`) thay vì đóng gói Chromium riêng, tiết kiệm ~685 MB cho bản .exe.
    Không có kênh nào thì lùi về Chromium của Playwright (nếu đã cài/nhúng).

    Đặt biến môi trường TNT_BROWSER_CHANNEL để ép 1 kênh cụ thể (msedge|chrome|
    chromium) khi cần.
    """
    forced = os.environ.get("TNT_BROWSER_CHANNEL", "").strip().lower()
    channels = [forced] if forced else ["msedge", "chrome", "chromium"]
    last = None
    for ch in channels:
        try:
            extra = {} if ch == "chromium" else {"channel": ch}
            return pw.chromium.launch_persistent_context(
                profile_dir, headless=headless, **extra, **kw)
        except Exception as e:
            last = e
    raise RuntimeError(
        "Không mở được trình duyệt (đã thử " + ", ".join(channels) + "). "
        "Cài Microsoft Edge hoặc Google Chrome, hoặc chạy "
        "'python -m playwright install chromium'. Chi tiết: " + str(last)[:150])


def use_bundled_browsers() -> None:
    """Bản .exe có nhúng Chromium -> trỏ Playwright vào bản đóng gói đó.

    Nhờ vậy máy đích chưa từng chạy `playwright install` vẫn dùng được chế độ
    bắt luồng bằng trình duyệt. Không ghi đè nếu người dùng đã tự đặt biến này.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    bdir = os.path.join(base, "ms-playwright")
    if os.path.isdir(bdir):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bdir


use_bundled_browsers()      # chạy sớm, trước khi bất kỳ chỗ nào gọi Playwright


def ffmpeg_path() -> str:
    """ffmpeg: ưu tiên bản đóng gói trong exe → hệ thống → bản kèm imageio-ffmpeg."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    _FFMPEG = _bundled("ffmpeg") or shutil.which("ffmpeg") or ""
    if not _FFMPEG:
        try:
            import imageio_ffmpeg  # type: ignore
            _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            _FFMPEG = "ffmpeg"
    return _FFMPEG


def ffprobe_path() -> str:
    """ffprobe hệ thống; không có thì thử cạnh ffmpeg (bản imageio không kèm ffprobe)."""
    global _FFPROBE
    if _FFPROBE:
        return _FFPROBE
    _FFPROBE = _bundled("ffprobe") or shutil.which("ffprobe") or ""
    if not _FFPROBE:
        cand = os.path.join(os.path.dirname(ffmpeg_path()), "ffprobe.exe"
                            if sys.platform == "win32" else "ffprobe")
        _FFPROBE = cand if os.path.exists(cand) else ""
    return _FFPROBE


def _has_video_via_ffmpeg(path: str) -> bool:
    """Kiểm tra luồng video bằng chính ffmpeg (khi không có ffprobe).

    `ffmpeg -i <file>` không xuất gì cả nhưng vẫn in thông tin luồng ra stderr —
    đủ để biết file có Video hay không. Nhờ vậy bản .exe chỉ cần nhúng ffmpeg,
    khỏi kèm thêm ffprobe (~97 MB).
    """
    try:
        p = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path],
                           capture_output=True, text=True, creationflags=_NO_WINDOW)
        return "Video:" in (p.stderr or "")
    except Exception:
        return False


def has_video_stream(path: str) -> bool:
    """File tải về có luồng VIDEO thật không (loại file ảnh/JSON/HTML rác).

    Thiếu cả ffprobe lẫn ffmpeg -> KHÔNG chặn oan: chỉ cần file đủ lớn và không
    phải HTML/JSON là cho qua.
    """
    if not path or not os.path.exists(path) or os.path.getsize(path) < 1024:
        return False
    ff = ffprobe_path()
    if not ff:
        if ffmpeg_path():
            return _has_video_via_ffmpeg(path)
        with open(path, "rb") as f:
            head = f.read(512).lstrip()[:64].lower()
        return not (head.startswith(b"<!doctype") or head.startswith(b"<html")
                    or head.startswith(b"{"))
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
        return "video" in out
    except Exception:
        return False


def probe_duration(path: str) -> float:
    """Thời lượng video (giây). Không có ffprobe -> 0, chỉ ảnh hưởng phần hiển thị."""
    ff = ffprobe_path()
    if not ff:
        return 0.0
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW).stdout.strip()
        return float(out or 0)
    except Exception:
        return 0.0


def run_ffmpeg(args: list[str]) -> None:
    """Chạy ffmpeg, ném lỗi kèm stderr rút gọn nếu fail."""
    p = subprocess.run([ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi: {(p.stderr or '').strip()[:200]}")


# ─────────────────────────── chuỗi / file ───────────────────────────
def clean_error(s) -> str:
    """Bỏ mã màu ANSI + tiền tố ERROR, rút gọn cho dễ đọc trên GUI."""
    s = ANSI_RE.sub("", str(s or ""))
    s = s.replace("ERROR:", "").replace("\n", " ").strip()
    return (s[:240] + "…") if len(s) > 240 else s


def safe_name(s: str, fallback: str = "video", limit: int = 70) -> str:
    """Tên file an toàn cho Windows (bỏ ký tự cấm, cắt ngắn)."""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", (s or "")).strip(" .")
    s = re.sub(r"\s+", " ", s)[:limit].strip(" .")
    return s or fallback


def unique_path(path: str) -> str:
    """Tránh ghi đè: thêm hậu tố (1), (2)... nếu file đã tồn tại."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def human_size(n: int | float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def open_folder(path: str) -> None:
    """Mở thư mục bằng file manager của hệ điều hành."""
    if not path or not os.path.isdir(path):
        return
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ───────────────────────────── cấu hình ─────────────────────────────
def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
