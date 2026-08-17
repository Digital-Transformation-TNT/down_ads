"""Nguồn cookies — chìa khoá để tải video quảng cáo / video riêng tư.

Vì sao cần: TikTok/Douyin quảng cáo (Spark Ads, link preview của Ads Manager,
video chỉ hiện với tài khoản được nhắm mục tiêu) KHÔNG có trên API công khai.
Chạy local mới có lợi thế này: dùng phiên đăng nhập của chính người dùng.

Có 3 nguồn cookies, xếp theo mức tin cậy:

1. File cookies.txt tự chọn        — luôn chạy được (xuất bằng extension
                                      "Get cookies.txt LOCALLY").
2. Hồ sơ trình duyệt TNT ("tnt")   — Chromium do app mở, người dùng đăng nhập
                                      1 lần. LUÔN đọc được vì là profile của
                                      chính mình. Cần Playwright.
3. Chrome/Edge/Firefox trên máy    — tiện nhất nhưng CHROME/EDGE TRÊN WINDOWS
                                      MỚI THƯỜNG THẤT BẠI: từ Chrome 127 cookies
                                      được mã hoá App-Bound Encryption, tiến
                                      trình ngoài giải mã không nổi (lỗi DPAPI —
                                      yt-dlp issue #10927). Firefox vẫn tốt.

`last_error()` giữ lý do thất bại gần nhất để GUI khuyên người dùng cho đúng.
"""

from __future__ import annotations

import os
import tempfile
import time

from .utils import BROWSER_PROFILE_DIR, launch_persistent

# Trình duyệt hệ thống mà yt-dlp đọc được cookies.
SUPPORTED = ["chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]
# Giá trị đặc biệt: lấy cookies từ hồ sơ Chromium riêng của app (nút "Mở trình
# duyệt để đăng nhập").
TNT_PROFILE = "tnt"

_JAR_CACHE: dict[str, object] = {}
_FILE_CACHE: dict[str, tuple[str, float]] = {}
_FILE_TTL = 600.0            # ghi lại file cookies mỗi 10 phút (phiên có thể được làm mới)
_LAST_ERROR = ""


def last_error() -> str:
    return _LAST_ERROR


# ───────────────────── 1. cookies từ trình duyệt hệ thống ─────────────────────
def cookiejar(browser: str):
    """CookieJar từ trình duyệt hệ thống (cache theo phiên chạy). Lỗi -> None."""
    global _LAST_ERROR
    b = (browser or "").strip().lower()
    if b not in SUPPORTED:
        return None
    if b in _JAR_CACHE:
        return _JAR_CACHE[b]
    jar = None
    try:
        from yt_dlp.cookies import extract_cookies_from_browser  # type: ignore
        jar = extract_cookies_from_browser(b)
    except Exception as e:
        msg = str(e)
        if "DPAPI" in msg or "10927" in msg:
            _LAST_ERROR = (
                f"Không đọc được cookies {b.capitalize()}: bản Windows/Chrome mới mã hoá "
                "cookies (App-Bound Encryption). Hãy dùng nút “Mở trình duyệt để đăng "
                "nhập” (hồ sơ TNT) hoặc chọn Firefox, hoặc xuất file cookies.txt.")
        else:
            _LAST_ERROR = f"Không đọc được cookies {b}: {msg[:160]}"
    _JAR_CACHE[b] = jar
    return jar


# ───────────────── 2. cookies từ hồ sơ Chromium riêng của app ─────────────────
def _write_netscape(cookies: list[dict], path: str) -> int:
    """Ghi list cookie kiểu Playwright ra file cookies.txt (định dạng Netscape)."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            dom = c.get("domain") or ""
            if not dom or not c.get("name"):
                continue
            expires = int(c.get("expires") or 0)
            if expires <= 0:                     # cookie phiên -> cho hạn xa để yt-dlp giữ lại
                expires = int(time.time()) + 86400 * 7
            f.write("\t".join([
                dom, "TRUE" if dom.startswith(".") else "FALSE",
                c.get("path") or "/", "TRUE" if c.get("secure") else "FALSE",
                str(expires), c["name"], c.get("value") or "",
            ]) + "\n")
            n += 1
    return n


def tnt_profile_cookiefile() -> str | None:
    """Xuất cookies từ hồ sơ Chromium của app ra cookies.txt.

    Đây là cách CHẮC ĂN trên Windows đời mới: profile do chính app tạo nên không
    dính App-Bound Encryption. Người dùng bấm “Mở trình duyệt để đăng nhập” một
    lần là dùng được mãi.
    """
    global _LAST_ERROR
    if not os.path.isdir(BROWSER_PROFILE_DIR):
        _LAST_ERROR = ("Chưa có hồ sơ trình duyệt TNT — bấm “Mở trình duyệt để đăng nhập” "
                       "rồi đăng nhập TikTok/Douyin một lần.")
        return None
    hit = _FILE_CACHE.get(TNT_PROFILE)
    if hit and os.path.exists(hit[0]) and (time.time() - hit[1]) < _FILE_TTL:
        return hit[0]
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            ctx = launch_persistent(p, BROWSER_PROFILE_DIR, headless=True)
            cks = ctx.cookies()
            ctx.close()
    except Exception as e:
        _LAST_ERROR = f"Không đọc được hồ sơ trình duyệt TNT: {str(e)[:160]}"
        return None
    path = os.path.join(tempfile.gettempdir(), "tnt_dl_cookies_profile.txt")
    if _write_netscape(cks or [], path) == 0:
        _LAST_ERROR = ("Hồ sơ trình duyệt TNT chưa có cookies — hãy đăng nhập TikTok/Douyin "
                       "trong cửa sổ trình duyệt của app rồi thử lại.")
        return None
    _FILE_CACHE[TNT_PROFILE] = (path, time.time())
    return path


# ──────────────────────────── điểm vào chung ────────────────────────────
def cookiefile(source: str) -> str | None:
    """File cookies.txt theo nguồn đã chọn ('tnt' | tên trình duyệt). Lỗi -> None.

    Ghi ra file thay vì đọc trực tiếp mỗi lần: giải mã cookies Chrome khá chậm,
    ghi 1 lần rồi dùng chung cho cả mẻ tải.
    """
    s = (source or "").strip().lower()
    if not s:
        return None
    if s == TNT_PROFILE:
        return tnt_profile_cookiefile()
    if s not in SUPPORTED:
        return None
    hit = _FILE_CACHE.get(s)
    if hit and os.path.exists(hit[0]) and (time.time() - hit[1]) < _FILE_TTL:
        return hit[0]
    jar = cookiejar(s)
    if jar is None:
        return None
    path = os.path.join(tempfile.gettempdir(), f"tnt_dl_cookies_{s}.txt")
    try:
        jar.save(path)                       # YoutubeDLCookieJar -> sẵn định dạng Netscape
    except Exception:
        try:                                 # jar chuẩn thư viện -> cần cờ ignore_*
            jar.save(path, ignore_discard=True, ignore_expires=True)  # type: ignore
        except Exception:
            return None
    _FILE_CACHE[s] = (path, time.time())
    return path


def load_into_session(session, cookies_path: str | None) -> None:
    """Nạp file cookies.txt vào một `requests.Session` (dùng cho quét trang quảng cáo)."""
    if not cookies_path or not os.path.exists(cookies_path):
        return
    try:
        from http.cookiejar import MozillaCookieJar
        jar = MozillaCookieJar(cookies_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)
    except Exception:
        pass


def available_browsers() -> list[str]:
    """Trình duyệt THỰC SỰ có hồ sơ trên máy (để đổ vào combobox)."""
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
    roaming = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    probes = {
        "chrome": [os.path.join(local, "Google", "Chrome", "User Data"),
                   os.path.join(home, "Library", "Application Support", "Google", "Chrome"),
                   os.path.join(home, ".config", "google-chrome")],
        "edge": [os.path.join(local, "Microsoft", "Edge", "User Data"),
                 os.path.join(home, "Library", "Application Support", "Microsoft Edge")],
        "firefox": [os.path.join(roaming, "Mozilla", "Firefox"),
                    os.path.join(home, "Library", "Application Support", "Firefox"),
                    os.path.join(home, ".mozilla", "firefox")],
        "brave": [os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
                  os.path.join(home, "Library", "Application Support",
                               "BraveSoftware", "Brave-Browser")],
        "opera": [os.path.join(roaming, "Opera Software", "Opera Stable")],
        "vivaldi": [os.path.join(local, "Vivaldi", "User Data")],
        "chromium": [os.path.join(local, "Chromium", "User Data"),
                     os.path.join(home, ".config", "chromium")],
    }
    return [name for name, paths in probes.items() if any(os.path.isdir(p) for p in paths)]
