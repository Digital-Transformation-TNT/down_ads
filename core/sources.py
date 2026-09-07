"""Danh sách site tải video — lấy từ xa để KHÔNG phải build lại app khi site chết.

Vì sao cần: sau khi đã chống được "site đổi giao diện" và "CDN đổi tên miền", chỗ
yếu cuối cùng còn lại là DANH SÁCH SITE. Site đóng cửa hay đổi tên miền là chuyện
thường; nếu danh sách nằm cứng trong code thì mỗi lần như vậy phải sửa code, build
lại và gửi app cho từng người.

Cách làm: danh sách nằm trong một file JSON công khai. Tool tải về, kiểm tra kỹ rồi
dùng; hỏng mạng hay file sai định dạng thì quay về danh sách nhúng sẵn — nên không
bao giờ tệ hơn hiện tại.

AN TOÀN: file này chỉ chứa DỮ LIỆU (tên site, URL, nhịp hỏi), không phải mã. Tool
không bao giờ eval/exec nội dung tải về, và chỉ nhận URL https của đúng các khoá đã
biết. Kẻ sửa được file cũng chỉ đổi được tool đi hỏi site nào.
"""

from __future__ import annotations

import json
import os
import time

from .utils import CONFIG_DIR, UA_DESKTOP

# Đặt URL file JSON ở đây (hoặc biến môi trường TNT_SOURCES_URL để đổi nhanh).
# Ví dụ: https://gist.githubusercontent.com/<user>/<id>/raw/sources.json
SOURCES_URL = ""

CACHE_PATH = os.path.join(CONFIG_DIR, "sources.json")
CACHE_TTL = 6 * 3600.0          # tải lại tối đa 6 tiếng 1 lần
_MEM: list[dict] | None = None


def _valid(sites) -> list[dict]:
    """Lọc bỏ mọi thứ không đúng dạng — thà dùng danh sách nhúng sẵn còn hơn dữ liệu lạ."""
    out: list[dict] = []
    if not isinstance(sites, list):
        return out
    for s in sites[:20]:
        if not isinstance(s, dict):
            continue
        name, home, api = s.get("name"), s.get("home"), s.get("api")
        if not (isinstance(name, str) and isinstance(home, str) and isinstance(api, str)):
            continue
        if not (home.startswith("https://") and api.startswith("https://")):
            continue
        try:
            gap = float(s.get("gap", 1.5))
        except (TypeError, ValueError):
            gap = 1.5
        out.append({"name": name[:30], "home": home[:300], "api": api[:300],
                    "gap": min(max(gap, 0.5), 30.0)})
    return out


def _read_cache() -> tuple[list[dict], float]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return _valid(d.get("sites")), float(d.get("saved_at") or 0)
    except Exception:
        return [], 0.0


def _write_cache(sites: list[dict]) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "sites": sites}, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch(url: str) -> list[dict]:
    import requests
    r = requests.get(url, timeout=10, headers={"User-Agent": UA_DESKTOP})
    r.raise_for_status()
    return _valid((r.json() or {}).get("sites"))


def load_sites(builtin: list[dict]) -> list[dict]:
    """Danh sách site sẽ dùng: bản từ xa nếu lấy được, không thì bản nhúng sẵn.

    Thứ tự: cache còn hạn -> tải mới -> cache cũ -> builtin. Luôn có kết quả, kể cả
    khi máy đang offline.
    """
    global _MEM
    if _MEM is not None:
        return _MEM

    url = (os.environ.get("TNT_SOURCES_URL") or SOURCES_URL).strip()
    if not url:
        _MEM = builtin
        return _MEM

    cached, saved_at = _read_cache()
    if cached and (time.time() - saved_at) < CACHE_TTL:
        _MEM = cached
        return _MEM
    try:
        fresh = _fetch(url)
        if fresh:
            _write_cache(fresh)
            _MEM = fresh
            return _MEM
    except Exception:
        pass
    _MEM = cached or builtin       # mạng hỏng -> bản cũ; chưa có gì -> bản nhúng sẵn
    return _MEM


def refresh() -> None:
    """Quên bản đang nhớ để lần sau tải lại (dùng khi người dùng bấm thử lại)."""
    global _MEM
    _MEM = None
