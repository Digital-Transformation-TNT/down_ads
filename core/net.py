"""Tự vượt chặn DNS — để một domain bị nhà mạng chặn không làm chết cả tool.

Vì sao cần: mạng ở VN hay chặn domain bằng cách trả về IP giả (127.0.0.1) cho
truy vấn DNS. Đã gặp thật với `savetik.co` — nguồn DUY NHẤT tải Douyin — nên tool
báo "Failed to establish a new connection" dù site vẫn sống bình thường.

Cách xử lý: khi DNS hệ thống trả IP giả (hoặc không phân giải được), hỏi lại qua
DNS-over-HTTPS (Google/Cloudflare). DoH đi bằng HTTPS tới IP của chính nhà cung
cấp DNS nên không bị chặn theo tên miền. Sau đó ghim IP vừa lấy cho riêng host đó.

Ghim bằng cách bọc `socket.getaddrinfo`: kết nối đi tới IP thật, nhưng tên miền
trong TLS (SNI) và header Host vẫn nguyên -> chứng chỉ vẫn khớp, không cần tắt
kiểm tra chứng chỉ.
"""

from __future__ import annotations

import socket
import threading

# IP mà nhà mạng hay trả về để chặn.
_BLOCKED = {"127.0.0.1", "0.0.0.0", "::1", "10.10.10.10"}

_PINNED: dict[str, str] = {}          # host -> IP đang dùng
_POOL: dict[str, list[str]] = {}      # host -> danh sách IP thật (để xoay khi bị reset)
_CHECKED: dict[str, bool] = {}        # host -> đã kiểm tra chưa
_LOCK = threading.Lock()
_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, port, *args, **kwargs):
    """Đổi host đã ghim sang IP; các host khác giữ nguyên hành vi cũ."""
    ip = _PINNED.get(host)
    return _orig_getaddrinfo(ip or host, port, *args, **kwargs)


socket.getaddrinfo = _patched_getaddrinfo


def _system_ip(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except Exception:
        return ""


def _doh_ips(host: str) -> list[str]:
    """Hỏi TẤT CẢ IP qua DNS-over-HTTPS (không dính chặn theo tên miền).

    Lấy cả danh sách chứ không chỉ 1 IP: nhà mạng còn chặn theo SNI và reset kết
    nối kiểu chập chờn, đổi sang IP khác của cùng dịch vụ thường là qua được.
    """
    import requests
    providers = [
        ("https://dns.google/resolve", {}),
        ("https://cloudflare-dns.com/dns-query", {"Accept": "application/dns-json"}),
    ]
    for url, headers in providers:
        try:
            r = requests.get(url, params={"name": host, "type": "A"},
                             headers=headers, timeout=12)
            r.raise_for_status()
            ips = [a.get("data", "") for a in (r.json().get("Answer") or [])
                   if a.get("type") == 1]
            ips = [ip for ip in ips if ip and ip not in _BLOCKED]
            if ips:
                return ips
        except Exception:
            continue
    return []


def ensure_host(host: str, force: bool = False) -> str:
    """Đảm bảo `host` phân giải ra IP dùng được. Trả về IP đã ghim ('' nếu không cần).

    force=True: XOAY sang IP khác trong danh sách — gọi khi kết nối vừa bị reset.
    """
    with _LOCK:
        if force and _POOL.get(host):
            pool = _POOL[host]
            cur = _PINNED.get(host)
            nxt = pool[(pool.index(cur) + 1) % len(pool)] if cur in pool else pool[0]
            _PINNED[host] = nxt
            return nxt
        if _CHECKED.get(host) and not force:
            return _PINNED.get(host, "")
        _CHECKED[host] = True
        ip = _system_ip(host)
        if ip and ip not in _BLOCKED:
            _PINNED.pop(host, None)         # DNS hệ thống ổn -> không cần ghim
            return ""
        ips = _doh_ips(host)
        if ips:
            _POOL[host] = ips
            _PINNED[host] = ips[0]
            return ips[0]
        return ""


def pinned() -> dict[str, str]:
    return dict(_PINNED)
