"""Nước cờ cuối cho VIDEO QUẢNG CÁO: mở trang bằng trình duyệt thật rồi "nghe" mạng.

Vì sao cần: quảng cáo Spark Ads / link preview của Ads Manager (TikTok Ads,
Ocean Engine - Douyin) render bằng JavaScript và thường đòi đăng nhập. Không API
công khai nào (tikwm, ssstik...) chạm tới được. Nhưng nếu MỞ đúng trang đó bằng
trình duyệt của chính người dùng thì video vẫn phát -> ta chỉ việc bắt lấy URL
media mà trang tải về.

Profile Chromium lưu tại ~/.tnt_downloader/browser_profile:
đăng nhập TikTok/Douyin/Ads Manager MỘT LẦN, các lần sau tự nhớ.

Cần: pip install playwright && python -m playwright install chromium
Thiếu Playwright -> engine tự bỏ qua bước này (không lỗi).
"""

from __future__ import annotations

import os
import re
import threading

from .utils import (BROWSER_PROFILE_DIR, UA_DESKTOP, has_video_stream,
                    launch_persistent, probe_duration, safe_name, unique_path)

# Chỉ 1 cửa sổ Chromium tại một thời điểm: mở nhiều cùng lúc vừa nặng máy vừa dễ
# loạn phiên đăng nhập (cùng dùng chung 1 profile).
_LOCK = threading.Lock()

_MEDIA_RE = re.compile(r"\.(mp4|m3u8)(\?|$)", re.I)
# Bỏ qua các luồng rác hay lẫn vào: ảnh động, sprite, quảng cáo của chính trang.
_SKIP_RE = re.compile(r"(sprite|thumb|cover|preview_?img|\.gif)", re.I)


def available() -> bool:
    try:
        import playwright  # noqa: F401  # type: ignore
        return True
    except Exception:
        return False


def sniff_download(url: str, out_dir: str, headless: bool = False,
                   wait_ms: int = 12000, on_log=None) -> dict:
    """Mở `url` trong Chromium (profile riêng), bắt URL video, tải về.

    headless=False (mặc định): thấy được cửa sổ -> người dùng tự đăng nhập /
    bấm play / qua captcha nếu trang đòi. Đây chính là lý do cách này ăn được
    quảng cáo mà server không làm nổi.
    """
    with _LOCK:
        return _sniff(url, out_dir, headless, wait_ms, on_log)


def _sniff(url: str, out_dir: str, headless: bool, wait_ms: int, on_log) -> dict:
    from playwright.sync_api import sync_playwright  # type: ignore

    def log(m):
        if on_log:
            on_log(m)

    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
    found: list[tuple[str, int]] = []      # (url, cỡ ước lượng)
    title = ""

    with sync_playwright() as p:
        ctx = launch_persistent(
            p, BROWSER_PROFILE_DIR, headless=headless, user_agent=UA_DESKTOP,
            viewport={"width": 1280, "height": 900},
            args=["--autoplay-policy=no-user-gesture-required",
                  "--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            u = resp.url
            ctype = (resp.headers or {}).get("content-type", "")
            if _SKIP_RE.search(u):
                return
            if _MEDIA_RE.search(u.split("#")[0]) or ctype.startswith("video/"):
                try:
                    size = int((resp.headers or {}).get("content-length") or 0)
                except Exception:
                    size = 0
                found.append((u, size))

        page.on("response", on_response)
        log("  mở trang bằng trình duyệt…")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass                                   # trang chậm/redirect -> vẫn nghe tiếp
        try:
            title = (page.title() or "").strip()
        except Exception:
            pass
        # Nhấn nút play nếu có (nhiều trang preview không tự phát).
        for sel in ("video", "[class*=play]", "[aria-label*=Play]", "button"):
            try:
                el = page.query_selector(sel)
                if el:
                    el.click(timeout=1500)
                    break
            except Exception:
                continue
        page.wait_for_timeout(wait_ms)
        # Lấy thêm src trong DOM (trường hợp video nằm sẵn trong HTML, không qua XHR).
        try:
            for src in page.eval_on_selector_all(
                    "video, video source",
                    "els => els.map(e => e.currentSrc || e.src).filter(Boolean)"):
                if src.startswith("http") and not _SKIP_RE.search(src):
                    found.append((src, 0))
        except Exception:
            pass

        if not found:
            ctx.close()
            raise RuntimeError("trình duyệt không thấy luồng video "
                               "(thử đăng nhập trong cửa sổ vừa mở rồi chạy lại)")

        # Bỏ trùng, ưu tiên: mp4 trước m3u8, file lớn trước (bản nét hơn).
        uniq: dict[str, int] = {}
        for u, size in found:
            uniq[u] = max(uniq.get(u, 0), size)
        cands = sorted(uniq.items(), key=lambda kv: (".m3u8" in kv[0].lower(), -kv[1]))
        log(f"  bắt được {len(cands)} luồng, đang tải…")

        name = safe_name(title, "video_quangcao")
        path = unique_path(os.path.join(out_dir, f"{name}.mp4"))
        last = None
        for u, _ in cands[:4]:
            try:
                if ".m3u8" in u.lower():
                    from .extractors import hls_download
                    hls_download(u, path, headers={"Referer": url})
                else:
                    # Tải bằng chính context của trình duyệt -> mang đủ cookies/headers.
                    resp = ctx.request.get(u, headers={"Referer": url}, timeout=120000)
                    if not resp.ok:
                        raise RuntimeError(f"HTTP {resp.status}")
                    with open(path, "wb") as f:
                        f.write(resp.body())
                if has_video_stream(path):
                    ctx.close()
                    return {"path": path, "title": title or name, "ext": "mp4",
                            "size": os.path.getsize(path),
                            "duration": probe_duration(path), "url": url,
                            "via": "browser-sniff"}
            except Exception as e:
                last = e
        ctx.close()
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(f"bắt được luồng nhưng tải không được ({last})")
