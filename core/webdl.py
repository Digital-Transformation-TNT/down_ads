"""Bóc video qua các site tải video (savetik, tiktokio…) — thiết kế để KHÔNG chết
khi site đổi giao diện.

Bài học từ hai lần hỏng trước:

1. Bóc theo cấu trúc trang thì site đổi là chết. Trang share iesdouyin bỏ
   `_ROUTER_DATA` -> hỏng; nhãn "MP4 HD" của savetik lại trỏ file 576p -> tải nhầm
   bản mờ. Nên ở đây KHÔNG dò theo thẻ/class/nhãn của bất kỳ site nào: chỉ quét
   MỌI url trông giống link media trong phản hồi, nằm đâu cũng được — HTML, JSON,
   chuỗi escape đều nhận.

2. Phụ thuộc một site là mong manh: savetik.co từng bị nhà mạng chặn (DNS trả
   127.0.0.1 + reset theo SNI) và có giới hạn tần suất. Nên đi lần lượt NHIỀU
   site; thêm site mới chỉ là thêm một dòng vào SITES.

Trả về link CDN gốc của Douyin/TikTok, hoặc link proxy của chính site đó — cái nào
tải ra video thật thì lấy.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Callable

import requests

from .net import ensure_host
from .utils import (UA_DESKTOP, clean_error, has_video_stream, probe_duration,
                    safe_name, unique_path)

Progress = Callable[[int, int], None] | None

# ─────────────────────────── danh sách site ───────────────────────────
# gap: nhịp tối thiểu giữa 2 lần hỏi CÙNG site (đo thực tế savetik chỉ chịu
# ~1 request/2s, gọi dồn là 429 hàng loạt).
SITES: list[dict] = [
    {"name": "savetik", "home": "https://savetik.co/vi",
     "api": "https://savetik.co/api/ajaxSearch", "gap": 2.5},
    {"name": "tiktokio", "home": "https://tiktokio.to/en",
     "api": "https://tiktokio.to/api/ajaxSearch", "gap": 1.5},
    {"name": "snapsave", "home": "https://snapsave.app/vn",
     "api": "https://snapsave.app/action.php", "gap": 1.5},
    {"name": "lovetik", "home": "https://lovetik.com/",
     "api": "https://lovetik.com/api/ajax/search", "gap": 1.5},
]

# Parser CHUNG: link media của Douyin/TikTok hoặc link proxy của site tải video.
# Không gắn với giao diện site nào nên site đổi HTML vẫn chạy.
MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>\\]{0,250}?"
    r"(?:douyinvod|zjcdn|douyinstatic|bytecdn|amemv|tikcdn|snapcdn\.app/get"
    r"|tikwm\.com/video|\.mp4)"
    # Đuôi phải RỘNG: token JWT của dl.snapcdn.app dài hơn 250 ký tự. Cắt cụt thì
    # link vừa hỏng vừa âm thầm rơi mất bản nét nhất (đã dính: tải về 576p trong
    # khi bản 1080p nằm ở link bị cắt).
    r"[^\s\"'<>\\]{0,2000}",
    re.I,
)
# Link chỉ là nhạc/ảnh bìa — không phải video cần tải.
SKIP_RE = re.compile(r"(music|/aweme/v1/play|cover|avatar|thumb|\.jpe?g|\.png|\.mp3)", re.I)

# ── Lưới cuối: nhận diện video KHÔNG dựa vào tên miền ──
# MEDIA_RE ở trên vẫn phải liệt kê tên miền CDN (zjcdn, douyinvod…) vì link Douyin
# không có đuôi .mp4. Ngày ByteDance đổi sang CDN tên khác là regex đó trượt sạch.
# Nên khi không khớp được gì, ta quét MỌI url trong phản hồi rồi HỎI SERVER xem cái
# nào là video (Content-Type). Cách này không cần biết trước tên miền nào cả.
ANY_URL_RE = re.compile(r"https?://[^\s\"'<>\\)]{10,2000}", re.I)
STATIC_RE = re.compile(
    r"\.(css|js|mjs|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|xml|txt)(\?|$)", re.I)
NOISE_RE = re.compile(
    r"(google|gstatic|doubleclick|facebook|twitter|cloudflare|jquery|bootstrap"
    r"|fontawesome|analytics|adsbygoogle|recaptcha|sentry|cdnjs)", re.I)

_LOCK = threading.Lock()
_LAST: dict[str, float] = {}


def _wait(site: dict) -> None:
    """Giữ nhịp hỏi cho TỪNG site, dùng chung cho mọi luồng tải."""
    name, gap = site["name"], site.get("gap", 1.5)
    with _LOCK:
        wait = gap - (time.time() - _LAST.get(name, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST[name] = time.time()


def _unescape(u: str) -> str:
    return (u.replace("\\u002F", "/").replace("\\u0026", "&")
             .replace("\\/", "/").replace("\\", "").replace("&amp;", "&"))


def _snapcdn_origin(link: str) -> str:
    """Link proxy dạng dl.snapcdn.app/get?token=<JWT> có chứa sẵn link CDN gốc.

    Token là JWT không mã hoá: phần payload base64 chứa {"url": "<link thật>"}.
    Bóc ra để lỡ proxy của họ hỏng thì vẫn tải thẳng được.
    """
    try:
        payload = link.split("token=", 1)[1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("url") or ""
    except Exception:
        return ""


def _sniff_video_urls(text: str, session: requests.Session,
                      limit: int = 12) -> list[str]:
    """Quét mọi url trong phản hồi rồi hỏi server cái nào là video.

    Đây là lưới cuối cho tình huống CDN/site đổi tên miền: không đoán theo tên,
    chỉ tải 1 byte đầu (Range) và xem Content-Type. Chỉ chạy khi cách nhanh phía
    trên không tìm được gì, nên không làm chậm đường chạy bình thường.
    """
    seen: list[str] = []
    for m in ANY_URL_RE.finditer(text):
        u = _unescape(m.group(0)).rstrip("\"',);")
        if (u in seen or STATIC_RE.search(u) or NOISE_RE.search(u)
                or SKIP_RE.search(u)):
            continue
        seen.append(u)
        if len(seen) >= 60:
            break
    found: list[str] = []
    for u in seen[:limit]:
        try:
            with session.get(u, stream=True, timeout=15,
                             headers={"User-Agent": UA_DESKTOP, "Range": "bytes=0-0"}) as r:
                ctype = (r.headers.get("Content-Type") or "").lower()
                clen = r.headers.get("Content-Range") or r.headers.get("Content-Length") or ""
            if ctype.startswith("video/") or ("octet-stream" in ctype and clen):
                found.append(u)
        except Exception:
            continue
    return found


def _ask_site(site: dict, ask_url: str, session: requests.Session) -> tuple[list[str], str]:
    """Hỏi 1 site -> (danh sách link media, tiêu đề). Ném lỗi nếu site không trả gì."""
    host = site["api"].split("//")[1].split("/")[0]
    ensure_host(host)                      # nhà mạng hay chặn DNS mấy site này
    hdr = {"User-Agent": UA_DESKTOP, "Referer": site["home"],
           "Origin": site["home"].rstrip("/").rsplit("/", 1)[0],
           "X-Requested-With": "XMLHttpRequest"}
    payload = {"q": ask_url, "url": ask_url, "query": ask_url, "lang": "vi"}

    text = ""
    last = ""
    for att in range(3):
        _wait(site)
        try:
            r = session.post(site["api"], data=payload, headers=hdr, timeout=30)
            if r.status_code == 429:
                last = "giới hạn tần suất (429)"
                time.sleep(3.0 * (att + 1))
                continue
            if r.status_code >= 400 or len(r.text) < 200:
                r = session.get(site["api"], params=payload, headers=hdr, timeout=30)
            text = r.text or ""
            if len(text) >= 200:
                break
            last = f"HTTP {r.status_code}, phản hồi rỗng"
        except Exception as e:
            last = clean_error(e)
            ensure_host(host, force=True)   # bị reset -> xoay sang IP khác
        time.sleep(1.0 * (att + 1))
    if not text:
        raise RuntimeError(last or "không có phản hồi")

    links: list[str] = []
    for m in MEDIA_RE.finditer(text):
        u = _unescape(m.group(0))
        if not SKIP_RE.search(u) and u not in links:
            links.append(u)
    if not links:
        # Không khớp tên miền nào đã biết -> có thể site/CDN vừa đổi. Hỏi server
        # từng url xem cái nào thật sự là video.
        links = _sniff_video_urls(text, session)
    title = ""
    mt = re.search(r"<h3[^>]*>([^<]{1,200})</h3>", text) or \
        re.search(r'"(?:title|desc)"\s*:\s*"([^"]{1,200})"', text)
    if mt:
        title = _unescape(mt.group(1)).strip()
    return links, title


def _probe_size(session: requests.Session, link: str) -> int:
    """Dung lượng thật của link, đo bằng 1 byte (Range) nên gần như không tốn gì.

    QUAN TRỌNG: chỉ đo trên link CDN GỐC. Token của dl.snapcdn.app dùng được đúng
    MỘT LẦN — hỏi trước là lúc tải thật nhận 500, đã dính rồi.
    """
    from .extractors import _referer_for
    hdr = {"User-Agent": UA_DESKTOP, "Range": "bytes=0-0"}
    ref = _referer_for(link)               # CDN douyin trả 403 nếu thiếu Referer
    if ref:
        hdr["Referer"] = ref
    try:
        with session.get(link, stream=True, timeout=20, headers=hdr) as r:
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def _measure(session: requests.Session, link: str) -> tuple[int, str]:
    """(dung lượng, link dùng để đo) — link snapcdn thì đo trên URL gốc trong token.

    Không tin nhãn chất lượng của site: đo thực tế thấy nhãn "MP4 HD" của savetik
    trỏ file 576p nhỏ hơn cả link thường 720p.
    """
    origin = _snapcdn_origin(link)
    probe = origin or link
    return _probe_size(session, probe), probe


def _headers_for(link: str, site: dict) -> dict:
    """Referer đúng theo ĐÍCH của link, không phải theo site đã cho link.

    Link CDN của Douyin (v5-dy…, zjcdn…) từ chối 403 nếu Referer là site tải video;
    nó cần Referer douyin.com. Chỉ link proxy của chính site mới cần Referer site.
    """
    h = {"User-Agent": UA_DESKTOP}
    if "snapcdn.app" in link.lower() or site["home"].split("//")[1].split("/")[0] in link:
        h["Referer"] = site["home"]
    return h                               # còn lại: stream_download tự chọn Referer


def _ask_url_for(url: str) -> str:
    """Dạng link mà các site nhận chắc nhất (Douyin -> trang chia sẻ)."""
    if "douyin.com" in url and "iesdouyin" not in url:
        m = re.search(r"/video/(\d{6,})", url) or re.search(r"modal_id=(\d{6,})", url)
        if m:
            return f"https://www.iesdouyin.com/share/video/{m.group(1)}/"
    return url


def download_via_sites(url: str, out_dir: str, session: requests.Session,
                       on_progress: Progress = None,
                       on_log: Callable[[str], None] | None = None) -> dict:
    """Thử lần lượt các site tới khi ra VIDEO THẬT. Ném lỗi nếu hết site."""
    from .extractors import stream_download        # tránh import vòng

    ask = _ask_url_for(url)
    m = re.search(r"/(?:video|photo)/(\d{6,})", url) or re.search(r"(\d{15,})", url)
    vid = m.group(1) if m else "video"
    errors: list[str] = []

    for site in SITES:
        try:
            links, title = _ask_site(site, ask, session)
        except Exception as e:
            errors.append(f"{site['name']}: {clean_error(e)[:70]}")
            continue
        if not links:
            errors.append(f"{site['name']}: không thấy link video")
            continue

        # Đo dung lượng thật rồi lấy bản TO NHẤT (= nét nhất). Với link proxy thì
        # đo trên URL gốc bóc từ token, giữ token nguyên vẹn cho lúc tải.
        measured = [(sz, probe, u) for u, (sz, probe) in
                    ((u, _measure(session, u)) for u in links[:6])]
        measured.sort(key=lambda t: -t[0])
        targets: list[str] = []
        for _sz, probe, u in measured:
            for cand in (probe, u):          # ưu tiên CDN gốc, hỏng thì qua proxy
                if cand and cand not in targets:
                    targets.append(cand)

        for target in targets[:8]:
            path = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
            try:
                stream_download(session, target, path,
                                headers=_headers_for(target, site),
                                on_progress=on_progress, timeout=120)
                if not has_video_stream(path):
                    os.remove(path)
                    continue
                if on_log:
                    on_log(f"     ✔ lấy được từ {site['name']}")
                return {"path": path, "title": title or vid, "ext": "mp4",
                        "size": os.path.getsize(path), "duration": probe_duration(path),
                        "url": url, "via": site["name"]}
            except Exception as e:
                errors.append(f"{site['name']}: tải lỗi {clean_error(e)[:50]}")

    raise RuntimeError("mọi site đều không ra video — " + " | ".join(errors[:4]))
