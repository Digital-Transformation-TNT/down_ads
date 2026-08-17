"""Bộ máy tải video — bản LOCAL, tối ưu lại từ backend web (services/downloader/ydl.py).

Khác biệt so với bản chạy trên server (Hugging Face Space):
1. KHÔNG cần relay/proxy: máy người dùng có IP dân dụng, gọi thẳng nguồn nào cũng được.
2. Mượn COOKIES trình duyệt trên máy -> tải được video riêng tư / quảng cáo mà
   server không bao giờ thấy.
3. Có nước cờ cuối "mở trình duyệt thật rồi bắt luồng" (sniffer) cho link quảng cáo.
4. Tải SONG SONG nhiều link (bản web chạy tuần tự từng link).
5. Chọn chất lượng / chỉ lấy MP3 / đặt thẳng thư mục lưu.

Thứ tự thử nguồn (dừng ở nguồn đầu tiên ra được video thật):
  link .mp4 thẳng   → tải luôn
  douyin            → iesdouyin → yt-dlp → quét trang → trình duyệt
  tiktok            → yt-dlp → tikwm → ssstik → trình duyệt
  trang quảng cáo   → quét trang → trình duyệt → yt-dlp generic
  còn lại           → yt-dlp → yt-dlp "best" → generic → quét trang → trình duyệt
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import extractors as ex
from . import sniffer
from .cookies import cookiefile
from .cookies import last_error as last_cookie_error
from .utils import URL_RE, clean_error, has_video_stream, probe_duration

# Trang preview quảng cáo (không có extractor riêng -> phải quét trang/trình duyệt).
_AD_HOSTS = ("ads.tiktok.com", "oceanengine.com", "toutiao.com", "adsmanager",
             "business.tiktok.com", "creativecenter")


@dataclass
class Options:
    out_dir: str = ""
    browser: str = ""            # tnt | chrome | edge | firefox | ... ('' = không cookies)
    concurrency: int = 3
    browser_fallback: bool = True     # bật nước cờ cuối (Playwright) cho link khó
    headless: bool = False            # cửa sổ hiện ra để người dùng đăng nhập/bấm play
    cookies_file: str = ""            # file cookies.txt tự chọn (ưu tiên hơn `browser`)


@dataclass
class Result:
    url: str
    ok: bool = False
    path: str = ""
    title: str = ""
    ext: str = ""
    size: int = 0
    duration: float = 0.0
    via: str = ""                     # nguồn nào tải được (để biết đường mà chỉnh)
    error: str = ""
    tried: list[str] = field(default_factory=list)   # nhật ký từng nguồn đã thử


# ───────────────────────────── link ─────────────────────────────
def _resolve_short(url: str) -> str:
    """Giải link rút gọn (vt/vm.tiktok.com, v.douyin.com) ra link thật.

    Link quảng cáo hầu hết ở dạng rút gọn -> chưa có '/video/<id>', mọi bước bóc
    id phía sau sẽ fail nếu không giải trước.
    """
    u = (url or "").strip()
    if not re.search(r"(vt|vm)\.tiktok\.com/|v\.douyin\.com/|t\.co/|bit\.ly/", u):
        return u
    try:
        import requests
        from .utils import UA_MOBILE
        h = {"User-Agent": UA_MOBILE}
        r = requests.head(u, allow_redirects=True, timeout=15, headers=h)
        final = r.url or u
        if final == u:                       # vài server chặn HEAD -> thử GET
            r = requests.get(u, allow_redirects=True, timeout=15, headers=h, stream=True)
            final = r.url or u
            r.close()
        return final
    except Exception:
        return u


def normalize_url(url: str) -> str:
    u = _resolve_short((url or "").strip())
    m = re.search(r"douyin\.com/.*?[?&]modal_id=(\d+)", u) or re.search(r"douyin\.com/video/(\d+)", u)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    return u


def parse_links(raw: str, max_links: int = 500, resolve: bool = False) -> list[str]:
    """Tách link từ ô dán: mỗi dòng 1 link, bỏ trùng, giữ thứ tự.

    resolve=False -> KHÔNG gọi mạng (dán 200 link vẫn hiện danh sách tức thì);
    việc giải link rút gọn để lúc tải mới làm.
    """
    seen, out = set(), []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = URL_RE.search(line)
        url = m.group(0) if m else line
        if not url.lower().startswith("http"):
            continue
        if resolve:
            url = normalize_url(url)
        if url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= max_links:
            break
    return out


def detect_source(url: str) -> str:
    u = url.lower()
    if any(h in u for h in _AD_HOSTS):
        return "ads"
    if ex.looks_direct_media(u):
        return "direct"
    table = {
        "youtube": ["youtube.com", "youtu.be"],
        "tiktok": ["tiktok.com"],
        "douyin": ["douyin.com", "iesdouyin.com", "douyinvod.com"],
        "facebook": ["facebook.com", "fb.watch", "fb.com"],
        "instagram": ["instagram.com"],
        "shopee": ["shopee."],
        "1688": ["1688.com"],
        "taobao": ["taobao.com", "tmall.com"],
        "drive": ["drive.google.com"],
    }
    for name, keys in table.items():
        if any(k in u for k in keys):
            return name
    return "other"


# ───────────────────────────── yt-dlp ─────────────────────────────
_IMPERSONATE = "unchecked"


def _impersonate_target():
    """ImpersonateTarget khả dụng (ưu tiên chrome) — hỏi yt-dlp 1 lần rồi cache.

    TikTok/Douyin nay đòi giả lập TLS trình duyệt mới trả luồng video. Máy thiếu
    curl_cffi -> trả None để bỏ qua êm, thay vì để yt-dlp ném lỗi cứng.
    """
    global _IMPERSONATE
    if _IMPERSONATE != "unchecked":
        return _IMPERSONATE
    _IMPERSONATE = None
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.networking.impersonate import ImpersonateTarget
        with YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            avail = y._get_available_impersonate_targets() or []
        if any("chrome" in str(t[0]).lower() for t in avail):
            _IMPERSONATE = ImpersonateTarget.from_str("chrome")
        elif avail:
            _IMPERSONATE = avail[0][0]
    except Exception:
        _IMPERSONATE = None
    return _IMPERSONATE


# Luôn lấy CHẤT LƯỢNG GỐC như trên link: video tốt nhất + tiếng tốt nhất, không có
# thì lấy file gộp sẵn tốt nhất (Douyin/TikTok thường chỉ có dạng gộp).
_FORMAT = "bv*+ba/b/best"

_REFERER = {
    "douyin": "https://www.douyin.com/",
    "tiktok": "https://www.tiktok.com/",
    "facebook": "https://www.facebook.com/",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://www.youtube.com/",
}


def resolve_cookies(opts: "Options") -> str | None:
    """File cookies.txt sẽ dùng: file tự chọn > nguồn đã chọn trong app.

    Kết quả được `core.cookies` cache nên gọi nhiều lần trong một mẻ tải vẫn rẻ.
    """
    if opts.cookies_file and os.path.exists(opts.cookies_file):
        return opts.cookies_file
    return cookiefile(opts.browser) if opts.browser else None


class _NullLogger:
    """Chặn yt-dlp in thẳng ra console — GUI tự hiển thị lỗi qua exception."""

    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _ydl_opts(opts: Options, source: str, generic: bool = False,
              fmt: str | None = None, on_progress=None) -> dict:
    # LƯU Ý: KHÔNG dùng tuỳ chọn `trim_file_name` của yt-dlp — nó cắt cả ĐƯỜNG DẪN
    # đầy đủ chứ không riêng tên file, nên thư mục lưu hơi sâu là file rơi ra ngoài
    # (đã dính lỗi này lúc test). Giới hạn độ dài đã làm bằng `%(title).70s`.
    from .utils import UA_DESKTOP
    headers = {"User-Agent": UA_DESKTOP}
    ref = _REFERER.get(source)
    if ref:
        headers["Referer"] = ref

    o: dict = {
        "outtmpl": os.path.join(opts.out_dir, "%(title).70s [%(id)s].%(ext)s"),
        "format": fmt or _FORMAT,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 8,   # local băng thông thoải mái -> kéo mạnh hơn server
        "http_headers": headers,
        "windowsfilenames": True,
        "noprogress": True,        # thanh % của yt-dlp -> để GUI tự vẽ, không rác console
        "logger": _NullLogger(),   # nuốt log/ERROR của yt-dlp; lỗi đã bắt qua exception
    }
    o["merge_output_format"] = "mp4"
    o["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    if on_progress:
        o["progress_hooks"] = [on_progress]
    if source in ("tiktok", "douyin"):
        tgt = _impersonate_target()
        if tgt is not None:
            o["impersonate"] = tgt
    if generic:
        o["force_generic_extractor"] = True

    cf = resolve_cookies(opts)
    if cf:
        o["cookiefile"] = cf
        if source == "youtube":
            # Client 'android' KHÔNG dùng cookies (cần PO token) -> có cookies thì
            # phải ưu tiên web/tv, không thì cookies bị bỏ qua và vẫn dính bot-check.
            o["extractor_args"] = {"youtube": {"player_client": ["web_safari", "web", "mweb", "tv"]}}
    return o


def _ydl_attempt(url: str, opts: Options, source: str, generic: bool = False,
                 fmt: str | None = None, on_progress=None) -> dict:
    import yt_dlp  # type: ignore

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            on_progress(int(d.get("downloaded_bytes") or 0),
                        int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0))

    with yt_dlp.YoutubeDL(_ydl_opts(opts, source, generic, fmt, hook)) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        base = os.path.splitext(path)[0]
        if not os.path.exists(path):
            for alt in (".mp4", ".mkv", ".webm", ".mov"):
                if os.path.exists(base + alt):
                    path = base + alt
                    break
        if not os.path.exists(path):
            raise RuntimeError("yt-dlp không tạo được file")
        if not has_video_stream(path):
            raise RuntimeError("file tải về không phải video xem được "
                               "(có thể là bài ảnh/slideshow hoặc trang không có video)")
        return {
            "path": path,
            "title": info.get("title") or os.path.basename(base),
            "ext": os.path.splitext(path)[1].lstrip(".") or "mp4",
            "size": os.path.getsize(path),
            "duration": float(info.get("duration") or 0) or probe_duration(path),
            "url": url, "via": "yt-dlp" + ("-generic" if generic else ""),
        }


# ───────────────────────── tải 1 link ─────────────────────────
def download_one(url: str, opts: Options, on_progress=None, on_log=None) -> Result:
    """Thử lần lượt các nguồn hợp với loại link; nguồn nào ra video thật thì dừng."""
    def log(m):
        if on_log:
            on_log(m)

    os.makedirs(opts.out_dir, exist_ok=True)
    res = Result(url=url)
    try:
        url = normalize_url(url)
    except Exception:
        pass
    res.url = url
    source = detect_source(url)
    cf = resolve_cookies(opts)
    sess = ex.make_session(cf)
    sess_m = ex.make_session(cf, mobile=True)

    # Danh sách nguồn theo loại link — mỗi phần tử: (tên, hàm chạy)
    steps: list[tuple[str, Callable[[], dict]]] = []
    if source == "direct":
        steps.append(("link trực tiếp", lambda: ex.direct_media(url, opts.out_dir, sess, on_progress)))
    elif source == "douyin":
        steps += [
            ("douyin (iesdouyin)", lambda: ex.douyin_share(url, opts.out_dir, sess_m, on_progress)),
            ("yt-dlp", lambda: _ydl_attempt(url, opts, source, on_progress=on_progress)),
            ("quét trang", lambda: ex.scrape_page(url, opts.out_dir, sess, on_progress)),
        ]
    elif source == "tiktok":
        steps += [
            ("yt-dlp", lambda: _ydl_attempt(url, opts, source, on_progress=on_progress)),
            ("tikwm", lambda: ex.tikwm(url, opts.out_dir, sess_m, on_progress)),
            ("ssstik", lambda: ex.ssstik(url, opts.out_dir, sess, on_progress)),
        ]
    elif source == "ads":
        steps += [
            ("quét trang quảng cáo", lambda: ex.scrape_page(url, opts.out_dir, sess, on_progress)),
            ("yt-dlp generic", lambda: _ydl_attempt(url, opts, source, generic=True,
                                                    on_progress=on_progress)),
        ]
    else:
        steps += [
            ("yt-dlp", lambda: _ydl_attempt(url, opts, source, on_progress=on_progress)),
            ("yt-dlp (best)", lambda: _ydl_attempt(url, opts, source, fmt="best",
                                                   on_progress=on_progress)),
        ]
        if source in ("shopee", "1688", "taobao", "other"):
            steps.append(("yt-dlp generic", lambda: _ydl_attempt(url, opts, source, generic=True,
                                                                 on_progress=on_progress)))
            steps.append(("quét trang", lambda: ex.scrape_page(url, opts.out_dir, sess, on_progress)))

    # Nước cờ cuối: mở trình duyệt thật, bắt luồng video (ăn được cả quảng cáo).
    if opts.browser_fallback and sniffer.available():
        steps.append(("trình duyệt (bắt luồng)",
                      lambda: sniffer.sniff_download(url, opts.out_dir,
                                                     headless=opts.headless, on_log=on_log)))

    for name, fn in steps:
        try:
            log(f"  → thử {name}…")
            d = fn()
            res.ok = True
            res.path = d["path"]
            res.title = d.get("title") or os.path.basename(d["path"])
            res.ext = d.get("ext") or os.path.splitext(d["path"])[1].lstrip(".")
            res.size = int(d.get("size") or 0)
            res.duration = float(d.get("duration") or 0)
            res.via = d.get("via") or name
            res.tried.append(f"{name}: OK")
            return res
        except Exception as e:
            msg = clean_error(e)
            res.tried.append(f"{name}: {msg}")
            log(f"     ✗ {name}: {msg}")

    hint = ""
    if source in ("tiktok", "douyin", "ads"):
        if opts.browser and not cf:
            hint = " | " + (last_cookie_error() or "Không lấy được cookies từ nguồn đã chọn.")
        elif not opts.browser:
            hint = (" | Mẹo: chọn nguồn Cookies (trình duyệt đang đăng nhập TikTok/Douyin) "
                    "rồi tải lại — video quảng cáo cần phiên đăng nhập.")
        elif not sniffer.available():
            hint = (" | Mẹo: cài Playwright để bật chế độ bắt luồng bằng trình duyệt: "
                    "pip install playwright && python -m playwright install chromium")
    res.error = clean_error(" | ".join(res.tried[-3:])) + hint
    return res


# ───────────────────────── tải nhiều link ─────────────────────────
def download_many(urls: list[str], opts: Options,
                  on_item_start: Callable[[int, str], None] | None = None,
                  on_item_progress: Callable[[int, int, int], None] | None = None,
                  on_item_done: Callable[[int, Result], None] | None = None,
                  on_log: Callable[[str], None] | None = None,
                  cancel: threading.Event | None = None) -> list[Result]:
    """Tải song song `concurrency` link cùng lúc, giữ nguyên thứ tự kết quả.

    Nguồn cần trình duyệt thật (sniffer) bị ép về TUẦN TỰ bằng khoá riêng: mở
    nhiều cửa sổ Chromium cùng lúc vừa nặng vừa dễ loạn đăng nhập.
    """
    results: list[Result] = [Result(url=u) for u in urls]
    n = len(urls)
    workers = max(1, min(int(opts.concurrency or 1), 8, n or 1))

    def job(i: int, url: str) -> None:
        if cancel and cancel.is_set():
            results[i].error = "đã huỷ"
            return
        if on_item_start:
            on_item_start(i, url)

        def prog(got: int, total: int) -> None:
            if on_item_progress:
                on_item_progress(i, got, total)

        def log(m: str) -> None:
            if on_log:
                on_log(f"[{i + 1}/{n}] {m}")

        try:
            r = download_one(url, opts, on_progress=prog, on_log=log)
        except Exception as e:                      # lưới an toàn: 1 link hỏng không sập cả mẻ
            r = Result(url=url, error=clean_error(e))
        results[i] = r
        if on_item_done:
            on_item_done(i, r)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda t: job(*t), list(enumerate(urls))))
    return results
