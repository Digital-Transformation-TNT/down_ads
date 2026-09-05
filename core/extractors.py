"""Các nguồn bóc video (chạy local, gọi thẳng - không cần relay/proxy).

Thứ tự dùng do `engine.py` quyết định. Mỗi hàm ở đây làm đúng 1 việc:
nhận URL -> trả về đường dẫn file đã tải, hoặc ném lỗi để engine thử nguồn sau.

Riêng cho VIDEO QUẢNG CÁO:
- `direct_media`  : link CDN thẳng (v3-ad-sign.douyinvod.com/..., *.mp4) mà bên
                    giao quảng cáo hay gửi -> tải luôn, không cần bóc.
- `scrape_page`   : trang preview quảng cáo (oceanengine, ads.tiktok...) -> quét
                    mọi link .mp4/.m3u8 trong HTML/JSON của trang.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from typing import Callable
from urllib.parse import urljoin

import requests

from .cookies import load_into_session
from .utils import (UA_DESKTOP, UA_MOBILE, clean_error, has_video_stream,
                    probe_duration, run_ffmpeg, safe_name, unique_path)

Progress = Callable[[int, int], None] | None

MEDIA_EXT_RE = re.compile(r"\.(mp4|mov|webm|m4v|mkv|m3u8)(\?|$)", re.I)
# Link media nằm trong HTML/JSON của trang (kể cả dạng escape \/ trong JSON).
MEDIA_IN_PAGE_RE = re.compile(
    r"""https?:[/\\]{1,2}[^\s"'<>()\\]+?\.(?:mp4|m3u8)(?:\?[^\s"'<>()\\]*)?""", re.I)
# Link media TƯƠNG ĐỐI trong thuộc tính/JSON: src="movie.mp4", "playUrl":"/a/b.m3u8"…
MEDIA_ATTR_RE = re.compile(
    r"""["'\s](?:src|href|url|video_?url|play_?url|playAddr)["']?\s*[:=]\s*["']"""
    r"""([^"'<>\s]+?\.(?:mp4|m3u8)(?:\?[^"'<>\s]*)?)["']""", re.I)


# ─────────────────────────── HTTP dùng chung ───────────────────────────
def make_session(cookies_path: str | None = None, mobile: bool = False) -> requests.Session:
    """Session có UA trình duyệt + (tuỳ chọn) cookies đã đăng nhập.

    `cookies_path` là file cookies.txt do `core.cookies.cookiefile()` dựng ra —
    cần cho việc quét trang preview quảng cáo (trang đòi đăng nhập).
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA_MOBILE if mobile else UA_DESKTOP,
        "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
    })
    load_into_session(s, cookies_path)
    return s


def _referer_for(url: str) -> str:
    u = url.lower()
    if "douyin" in u or "bytecdn" in u or "byteimg" in u:
        return "https://www.douyin.com/"
    if "tiktok" in u:
        return "https://www.tiktok.com/"
    if "oceanengine" in u or "toutiao" in u:
        return "https://ad.oceanengine.com/"
    return ""


def stream_download(session: requests.Session, url: str, path: str,
                    headers: dict | None = None, on_progress: Progress = None,
                    timeout: int = 60) -> str:
    """Tải 1 URL về file, báo tiến độ. Ghi ra .part rồi đổi tên -> không để lại file dở."""
    h = dict(headers or {})
    ref = _referer_for(url)
    if ref:
        h.setdefault("Referer", ref)
    tmp = path + ".part"
    got = 0
    try:
        with session.get(url, headers=h, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 17):
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got, total)
    except BaseException:
        try:                       # tải dở -> dọn .part, đừng để rác lại cho người dùng
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    return path


def hls_download(m3u8_url: str, path: str, headers: dict | None = None) -> str:
    """Tải playlist HLS (.m3u8) bằng ffmpeg -> mp4 (copy stream, không encode lại)."""
    h = dict(headers or {})
    h.setdefault("User-Agent", UA_DESKTOP)
    ref = _referer_for(m3u8_url)
    if ref:
        h.setdefault("Referer", ref)
    hdr = "".join(f"{k}: {v}\r\n" for k, v in h.items())
    run_ffmpeg(["-headers", hdr, "-i", m3u8_url, "-c", "copy",
                "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", path])
    return path


def _finish(path: str, title: str, url: str, via: str) -> dict:
    """Kiểm tra file là video thật rồi đóng gói kết quả cho engine."""
    if not has_video_stream(path):
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError("file tải về không phải video xem được")
    return {
        "path": path, "title": title or os.path.splitext(os.path.basename(path))[0],
        "ext": os.path.splitext(path)[1].lstrip("."), "size": os.path.getsize(path),
        "duration": probe_duration(path), "url": url, "via": via,
    }


# ─────────────────────── 1. Link media trực tiếp ───────────────────────
def looks_direct_media(url: str) -> bool:
    """URL trỏ thẳng tới file video? (link CDN quảng cáo hay ở dạng này)"""
    return bool(MEDIA_EXT_RE.search(url.split("#")[0]))


def direct_media(url: str, out_dir: str, session: requests.Session,
                 on_progress: Progress = None) -> dict:
    """Tải thẳng link video CDN (kể cả link quảng cáo dạng *.mp4 / *.m3u8)."""
    name = safe_name(os.path.basename(url.split("?")[0].split("#")[0]) or "video")
    base = os.path.splitext(name)[0] or "video"
    path = unique_path(os.path.join(out_dir, base + ".mp4"))
    if ".m3u8" in url.lower():
        hls_download(url, path)
    else:
        stream_download(session, url, path, on_progress=on_progress, timeout=120)
    return _finish(path, base, url, "direct")


# ───────────────────────── 2. Douyin (share JSON) ─────────────────────────
def douyin_share(url: str, out_dir: str, session: requests.Session,
                 on_progress: Progress = None) -> dict:
    """Bóc Douyin qua trang chia sẻ iesdouyin — KHÔNG cần cookies.

    Extractor Douyin của yt-dlp nay đòi cookies "tươi"; cách này lấy JSON
    `_ROUTER_DATA` trong trang mobile rồi tải thẳng, đổi 'playwm' -> 'play'
    để lấy bản KHÔNG watermark.
    """
    m = re.search(r"/video/(\d+)", url) or re.search(r"modal_id=(\d+)", url)
    if not m:
        raise RuntimeError("không tìm được id video douyin")
    vid = m.group(1)
    html = session.get(f"https://www.iesdouyin.com/share/video/{vid}/",
                       headers={"User-Agent": UA_MOBILE}, timeout=25).text
    mm = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not mm:
        raise RuntimeError("không đọc được dữ liệu trang share douyin")
    data = json.loads(mm.group(1))

    urls: list[str] = []
    title = {"v": ""}

    def walk(o):
        if isinstance(o, dict):
            pa = o.get("play_addr")
            if isinstance(pa, dict):
                urls.extend(pa.get("url_list", []))
            if not title["v"] and isinstance(o.get("desc"), str):
                title["v"] = o["desc"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    play = [u if u.startswith("http") else "https:" + u for u in urls]
    if not play:
        raise RuntimeError("không có link video (có thể là bài ảnh/slideshow)")
    ttl = (title["v"] or vid).strip()
    path = unique_path(os.path.join(out_dir, f"{safe_name(ttl, vid)} [{vid}].mp4"))
    last = None
    for src in play[:3]:                       # url_list là nhiều CDN của cùng 1 video
        try:
            stream_download(session, src.replace("playwm", "play"), path,
                            headers={"User-Agent": UA_MOBILE},
                            on_progress=on_progress, timeout=120)
            return _finish(path, ttl, url, "douyin-share")
        except Exception as e:
            last = e
    raise RuntimeError(f"tải douyin thất bại: {clean_error(last)}")


# ───────────────────────── 3. TikTok qua tikwm ─────────────────────────
def _abs_tikwm(u: str) -> str:
    u = u or ""
    return u if u.startswith("http") else (("https://www.tikwm.com" + u) if u else "")


def tikwm(url: str, out_dir: str, session: requests.Session,
          on_progress: Progress = None) -> dict:
    """Nguồn phụ TikTok (giống SnapTik): video no-watermark, hoặc dựng slideshow ảnh."""
    headers = {
        "User-Agent": UA_MOBILE,
        "Referer": "https://www.tikwm.com/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    endpoints = [("get", "https://www.tikwm.com/api/"),
                 ("post", "https://www.tikwm.com/api/"),
                 ("get", "https://tikwm.com/api/")]
    last = ""
    data = None
    for att in range(3):
        for method, ep in endpoints:
            try:
                kw = {"timeout": 25, "headers": headers}
                r = (session.get(ep, params={"url": url, "hd": 1}, **kw) if method == "get"
                     else session.post(ep, data={"url": url, "hd": 1}, **kw))
                r.raise_for_status()
                j = r.json()
                if j.get("code") == 0 and j.get("data"):
                    data = j["data"]
                    break
                last = str(j.get("msg") or f"code {j.get('code')}")
            except Exception as e:
                last = clean_error(e)
        if data:
            break
        if att < 2:                    # "Free Api Limit" / chập chờn -> chờ rồi thử lại
            time.sleep(1.5 * (att + 1))
    if not data:
        raise RuntimeError(f"tikwm báo lỗi: {last}")

    title = (data.get("title") or "").strip()
    m = (re.search(r"/(?:video|photo)/(\d{6,})", url)
         or re.search(r"item_id=(\d{6,})", url) or re.search(r"(\d{15,})", url))
    vid = str(data.get("id") or (m.group(1) if m else "tiktok"))
    out = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
    dur = float(data.get("duration") or 0)
    images = data.get("images") or []

    if images:
        _build_slideshow(session, images, _abs_tikwm(data.get("music") or ""), dur, out)
    else:
        vurl = _abs_tikwm(data.get("hdplay") or data.get("play") or "")
        if not vurl:
            raise RuntimeError("tikwm không trả link video")
        stream_download(session, vurl, out, headers=headers,
                        on_progress=on_progress, timeout=120)
    return _finish(out, title or vid, url, "tikwm")


def _build_slideshow(session: requests.Session, images: list, music_url: str,
                     dur: float, out: str) -> None:
    """Bài ẢNH/slideshow -> ghép ảnh + nhạc thành mp4 khung dọc 1080x1920."""
    tmp = tempfile.mkdtemp(prefix="tnt_slide_")
    paths = []
    for i, im in enumerate(images):
        p = os.path.join(tmp, f"img_{i:03d}.jpg")
        stream_download(session, _abs_tikwm(im), p, timeout=30)
        paths.append(p)
    if not paths:
        raise RuntimeError("slideshow không có ảnh")
    music = os.path.join(tmp, "music.mp3")
    if music_url:
        try:
            stream_download(session, music_url, music, timeout=30)
        except Exception:
            music = ""
    else:
        music = ""
    per = max(1.0, (dur or len(paths) * 3.0) / len(paths))
    listf = os.path.join(tmp, "list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{p.replace(os.sep, '/')}'\nduration {per:.3f}\n")
        f.write(f"file '{paths[-1].replace(os.sep, '/')}'\n")   # concat cần lặp ảnh cuối
    args = ["-f", "concat", "-safe", "0", "-i", listf]
    if music:
        args += ["-i", music, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
    args += ["-vf", ("scale=1080:1920:force_original_aspect_ratio=decrease,"
                     "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-movflags", "+faststart", out]
    run_ffmpeg(args)


# ───────────────────────── 4. TikTok qua ssstik ─────────────────────────
def ssstik(url: str, out_dir: str, session: requests.Session,
           on_progress: Progress = None) -> dict:
    """Nguồn phụ TikTok thứ 2 — hạ tầng riêng, KHÔNG dính quota của tikwm."""
    home = session.get("https://ssstik.io/en", timeout=25,
                       headers={"User-Agent": UA_DESKTOP, "Accept": "text/html"}).text
    tm = (re.search(r'name="tt"\s+value="([^"]+)"', home)
          or re.search(r'tt:\s*"([^"]+)"', home)
          or re.search(r"\btt=([A-Za-z0-9_-]{6,})", home))
    r = session.post("https://ssstik.io/abc?url=dl", timeout=30, data={
        "id": url, "locale": "en", "tt": tm.group(1) if tm else "",
    }, headers={
        "User-Agent": UA_DESKTOP,
        "HX-Request": "true", "HX-Current-URL": "https://ssstik.io/en",
        "Referer": "https://ssstik.io/en", "Origin": "https://ssstik.io",
    })
    frag = r.text
    hrefs = re.findall(r'href="(https?://[^"]+)"', frag)
    vurl = next((h for h in hrefs
                 if re.search(r"tikcdn|\.mp4|tikwm|no_watermark|type=video", h, re.I)), "")
    if not vurl:
        raise RuntimeError("ssstik: không thấy link video")
    tt = re.search(r"<h2[^>]*>([^<]+)</h2>", frag) or re.search(r'class="maintext"[^>]*>([^<]+)<', frag)
    title = tt.group(1).strip() if tt else ""
    m = re.search(r"/(?:video|photo)/(\d{6,})", url) or re.search(r"(\d{15,})", url)
    vid = m.group(1) if m else "tiktok"
    out = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
    stream_download(session, vurl, out, on_progress=on_progress, timeout=120)
    return _finish(out, title or vid, url, "ssstik")


# ───────────────────── 5. Quét link media trong trang ─────────────────────
def scrape_page(url: str, out_dir: str, session: requests.Session,
                on_progress: Progress = None) -> dict:
    """Tải HTML trang rồi quét mọi link .mp4/.m3u8 — dùng cho TRANG PREVIEW QUẢNG CÁO.

    Trang preview của Ads Manager / Ocean Engine không có extractor riêng, nhưng
    link video thường nằm ngay trong HTML hoặc JSON nhúng. Có cookies trình duyệt
    thì đọc được cả trang yêu cầu đăng nhập.
    """
    r = session.get(url, timeout=30, headers={"Referer": _referer_for(url) or url})
    r.raise_for_status()
    html = r.text
    def _clean(u: str) -> str:
        return (u.replace("\\u002F", "/").replace("\\u0026", "&")
                 .replace("\\/", "/").replace("\\", "/").replace("&amp;", "&"))

    cands: list[str] = []
    for m in MEDIA_IN_PAGE_RE.finditer(html):
        u = _clean(m.group(0))
        if u not in cands:
            cands.append(u)
    # Thêm link tương đối (src="movie.mp4") -> ghép với địa chỉ trang.
    for m in MEDIA_ATTR_RE.finditer(html):
        u = _clean(m.group(1))
        if not u.lower().startswith("http"):
            u = urljoin(str(r.url), u)
        if u not in cands:
            cands.append(u)
    if not cands:
        raise RuntimeError("không thấy link video trong trang")
    # Ưu tiên link có vẻ là bản chính (playwm = có watermark -> để sau cùng).
    cands.sort(key=lambda u: ("playwm" in u, ".m3u8" in u.lower()))
    title = ""
    tm = re.search(r"<title[^>]*>([^<]{1,120})</title>", html, re.I)
    if tm:
        title = tm.group(1).strip()
    last = None
    for i, u in enumerate(cands[:5]):
        path = unique_path(os.path.join(out_dir, f"{safe_name(title, 'video')}.mp4"))
        try:
            if ".m3u8" in u.lower():
                hls_download(u, path)
            else:
                stream_download(session, u, path, on_progress=on_progress, timeout=120)
            return _finish(path, title, url, "page-scrape")
        except Exception as e:
            last = e
    raise RuntimeError(f"quét trang: mọi link đều lỗi ({clean_error(last)})")


# ───────────── 6. API web nội bộ của TikTok (chạy bằng cookies) ─────────────
def tiktok_web_api(url: str, out_dir: str, session: requests.Session,
                   on_progress: Progress = None) -> dict:
    """Gọi thẳng API mà trang tiktok.com dùng — KHÔNG mở trình duyệt.

    Đây là đường tải VIDEO QUẢNG CÁO không cần bật Chrome: cùng một endpoint mà
    trang web gọi, chỉ khác là ta gửi kèm cookies đã đăng nhập của người dùng nên
    thấy được đúng những gì họ thấy. Không có cookies thì TikTok hay trả rỗng ->
    engine tự lùi sang tikwm/ssstik.
    """
    m = (re.search(r"/(?:video|photo)/(\d{6,})", url)
         or re.search(r"item_id=(\d{6,})", url) or re.search(r"(\d{15,})", url))
    if not m:
        raise RuntimeError("không tách được id video tiktok")
    vid = m.group(1)
    r = session.get(
        "https://www.tiktok.com/api/item/detail/",
        params={"itemId": vid, "aid": "1988", "app_name": "tiktok_web",
                "device_platform": "web_pc", "channel": "tiktok_web",
                "app_language": "vi-VN", "region": "VN", "language": "vi"},
        headers={"User-Agent": UA_DESKTOP, "Referer": url,
                 "Accept": "application/json, text/plain, */*"},
        timeout=25)
    r.raise_for_status()
    try:
        j = r.json()
    except Exception:
        raise RuntimeError("API tiktok trả về không phải JSON (thiếu cookies?)")
    item = ((j.get("itemInfo") or {}).get("itemStruct")) or {}
    video = item.get("video") or {}
    if not item:
        raise RuntimeError(f"API tiktok không có dữ liệu (status {j.get('statusCode')})")

    # Gom mọi link phát: bitrateInfo (nhiều mức nét) trước, rồi playAddr/downloadAddr.
    cands: list[str] = []
    for b in (video.get("bitrateInfo") or []):
        cands += ((b.get("PlayAddr") or {}).get("UrlList") or [])
    for key in ("playAddr", "downloadAddr"):
        if video.get(key):
            cands.append(video[key])
    cands = [u for u in dict.fromkeys(cands) if u]
    if not cands:
        raise RuntimeError("API tiktok không trả link phát (có thể là bài ảnh)")

    title = (item.get("desc") or "").strip()
    out = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
    last = None
    for u in cands[:4]:
        try:
            stream_download(session, u, out,
                            headers={"User-Agent": UA_DESKTOP,
                                     "Referer": "https://www.tiktok.com/"},
                            on_progress=on_progress, timeout=120)
            return _finish(out, title or vid, url, "tiktok-api")
        except Exception as e:
            last = e
    raise RuntimeError(f"tải link từ API tiktok thất bại: {clean_error(last)}")


# ───────────── 7. API web nội bộ của Douyin (chạy bằng cookies) ─────────────
_DY_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"


def douyin_web_api(url: str, out_dir: str, session: requests.Session,
                   on_progress: Progress = None) -> dict:
    """Gọi thẳng API mà trang douyin.com dùng — KHÔNG mở trình duyệt.

    Từ 2026 Douyin bỏ hẳn dữ liệu video khỏi trang share iesdouyin (chỉ còn
    metadata trang), nên cách bóc HTML cũ chết. API này là đường còn lại, nhưng
    có "ArgusSecurityPlugin" chặn request thiếu cookies của trình duyệt thật.

    Hai điều đo được khi thử nghiệm, quyết định cách viết hàm này:
    1. Cookies douyin trong hồ sơ trình duyệt dùng lại được nhiều lần, kể cả
       cookies lấy từ trước đó khá lâu — không cần lấy mới mỗi lần tải.
    2. Server chặn NGẪU NHIÊN, xen kẽ 403/200 ngay trong cùng một phiên. Nên
       gặp 403 thì cứ thử lại, đừng vội bỏ sang nguồn khác.
    """
    m = (re.search(r"/video/(\d+)", url) or re.search(r"modal_id=(\d+)", url)
         or re.search(r"(\d{15,})", url))
    if not m:
        raise RuntimeError("không tìm được id video douyin")
    vid = m.group(1)
    params = {"aweme_id": vid, "device_platform": "webapp", "aid": "6383",
              "channel": "channel_pc_web", "pc_client_type": "1",
              "version_code": "190500", "version_name": "19.5.0",
              "cookie_enabled": "true", "platform": "PC"}
    headers = {"User-Agent": UA_DESKTOP,
               "Referer": f"https://www.douyin.com/video/{vid}",
               "Accept": "application/json, text/plain, */*"}

    detail = None
    last = ""
    for att in range(5):                 # chặn ngẫu nhiên -> kiên nhẫn thử lại
        try:
            r = session.get(_DY_API, params=params, timeout=25, headers=headers)
            if r.status_code == 200 and len(r.text) > 500:
                detail = (r.json() or {}).get("aweme_detail")
                if detail:
                    break
            last = f"HTTP {r.status_code}: {r.text[:60].strip()}"
        except Exception as e:
            last = clean_error(e)
        time.sleep(1.0 + 0.8 * att)
    if not detail:
        raise RuntimeError(
            f"API douyin chặn ({last}). Cần cookies douyin: chọn Cookies = "
            "“Trình duyệt TNT” và ghé douyin.com một lần bằng nút “Mở trình duyệt”.")

    v = detail.get("video") or {}
    urls: list[str] = list((v.get("play_addr") or {}).get("url_list") or [])
    for b in (v.get("bit_rate") or []):          # nhiều mức nét, lấy hết rồi thử lần lượt
        urls += ((b.get("play_addr") or {}).get("url_list") or [])
    for key in ("play_addr_h264", "play_addr_265", "download_addr"):
        urls += ((v.get(key) or {}).get("url_list") or [])
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        raise RuntimeError("API douyin không trả link phát (có thể là bài ảnh)")

    title = (detail.get("desc") or "").strip()
    out = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
    last_err = None
    for u in urls[:5]:
        try:
            stream_download(session, u, out,
                            headers={"User-Agent": UA_DESKTOP,
                                     "Referer": "https://www.douyin.com/"},
                            on_progress=on_progress, timeout=120)
            return _finish(out, title or vid, url, "douyin-api")
        except Exception as e:
            last_err = e
    raise RuntimeError(f"tải link từ API douyin thất bại: {clean_error(last_err)}")


# ──────── 8. savetik / snapcdn — kiểu snaptik, KHÔNG cần cookies ────────
_SAVETIK_HOME = "https://savetik.co/vi"
_SAVETIK_API = "https://savetik.co/api/ajaxSearch"
_SNAPCDN_RE = re.compile(r'<a[^>]+href="(https://dl\.snapcdn\.app/get\?token=[^"]+)"[^>]*>(.{0,80}?)</a>',
                         re.S | re.I)


def _snapcdn_origin(link: str) -> str:
    """Bóc link CDN GỐC nằm trong token JWT của snapcdn (để tải thẳng nếu cần).

    Token là JWT không mã hoá: phần payload base64 chứa {"url": "<link CDN thật>"}.
    """
    try:
        payload = link.split("token=", 1)[1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("url") or ""
    except Exception:
        return ""


def savetik(url: str, out_dir: str, session: requests.Session,
            on_progress: Progress = None) -> dict:
    """Bóc video qua savetik.co — cùng cách mà các site snaptik/savetik đang dùng.

    Vì sao cần: từ 2026 Douyin bỏ dữ liệu video khỏi trang share và khoá API bằng
    ArgusSecurityPlugin, nên mọi cách bóc trực tiếp đều chết nếu không có cookies
    trình duyệt. Các site tải video vẫn sống vì họ có hạ tầng riêng đã qua được
    lớp chặn đó; ta hỏi họ và nhận về link CDN thật.

    Trả về link dạng dl.snapcdn.app/get?token=<JWT> — trong JWT có sẵn link CDN
    gốc, nên hỏng proxy của họ thì vẫn tải thẳng được.
    """
    hdr = {"User-Agent": UA_DESKTOP, "Referer": _SAVETIK_HOME,
           "Origin": "https://savetik.co", "X-Requested-With": "XMLHttpRequest"}
    try:
        session.get(_SAVETIK_HOME, timeout=20, headers={"User-Agent": UA_DESKTOP})
    except Exception:
        pass                                   # không lấy được trang chủ vẫn thử API

    data = None
    last = ""
    for att in range(3):
        try:
            r = session.post(_SAVETIK_API, data={"q": url, "lang": "vi"},
                             headers=hdr, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("status") == "ok" and j.get("data"):
                data = j["data"]
                break
            last = str(j.get("mess") or j.get("status") or "")[:80]
        except Exception as e:
            last = clean_error(e)
        time.sleep(1.0 + att)
    if not data:
        raise RuntimeError(f"savetik không trả dữ liệu ({last})")

    title = ""
    mt = re.search(r"<h3[^>]*>([^<]{1,200})</h3>", data)
    if mt:
        title = mt.group(1).strip()

    # Gom link, bỏ nhạc (MP3).
    links: list[str] = []
    for m in _SNAPCDN_RE.finditer(data):
        label = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip().lower()
        if "mp3" in label or "audio" in label or "nhạc" in label:
            continue
        links.append(m.group(1))
    if not links:
        raise RuntimeError("savetik không có link video (có thể là bài ảnh)")

    # NHÃN CỦA SAVETIK KHÔNG ĐÁNG TIN: đo thực tế thấy link ghi "MP4 HD" lại là
    # bản 576p (3.7MB) còn "MP4 [1]" mới là 720p (5.4MB). Vì vậy hỏi dung lượng
    # thật rồi lấy file to nhất — vài request nhẹ, đổi lại không tải nhầm bản mờ.
    def _remote_size(link: str) -> int:
        try:
            with session.get(link, stream=True, timeout=20,
                             headers={"User-Agent": UA_DESKTOP,
                                      "Referer": "https://savetik.co/"}) as r:
                r.raise_for_status()
                return int(r.headers.get("Content-Length") or 0)
        except Exception:
            return 0

    cands = sorted(((-_remote_size(u), u) for u in links[:4]), key=lambda x: x[0])

    m = re.search(r"/(?:video|photo)/(\d{6,})", url) or re.search(r"(\d{15,})", url)
    vid = m.group(1) if m else "video"
    out = unique_path(os.path.join(out_dir, f"{safe_name(title, vid)} [{vid}].mp4"))
    last_err = None
    for _, link in cands[:3]:
        for target in (link, _snapcdn_origin(link)):     # proxy trước, CDN gốc sau
            if not target:
                continue
            try:
                stream_download(session, target, out,
                                headers={"User-Agent": UA_DESKTOP,
                                         "Referer": "https://savetik.co/"},
                                on_progress=on_progress, timeout=120)
                return _finish(out, title or vid, url, "savetik")
            except Exception as e:
                last_err = e
    raise RuntimeError(f"tải link savetik thất bại: {clean_error(last_err)}")
