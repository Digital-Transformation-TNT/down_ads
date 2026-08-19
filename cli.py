"""Bản dòng lệnh — tiện khi cần tải hàng loạt tự động (không cần mở GUI).

Ví dụ:
  python cli.py -o D:\\videos links.txt
  python cli.py -o D:\\videos --browser chrome "https://www.tiktok.com/@a/video/123"
  python cli.py -o D:\\videos --jobs 5 links.txt
"""

from __future__ import annotations

import argparse
import os
import sys

from core.engine import Options, download_many, parse_links
from core.utils import human_size
from tnt_license import LicenseError, check_license, machine_id_pretty

TOOL_NAME = "TNT_Downloader"     # phải khớp với app.py và với license đã cấp


def main() -> int:
    # Console Windows mặc định cp1252 -> in tiêu đề tiếng Việt/ký hiệu ✔ sẽ nổ.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    # Bản CLI tự xử lý lỗi license (raise_on_error) và IN ra console — không mở hộp
    # thoại như bản GUI, vì CLI hay chạy tự động/không có màn hình.
    try:
        check_license(TOOL_NAME, raise_on_error=True)
    except LicenseError as e:
        print(f"[LICENSE] {e}")
        print(f"[LICENSE] Mã máy: {machine_id_pretty()}")
        print("[LICENSE] Gửi mã máy này để được cấp license.key, "
              "rồi đặt file cạnh tool (hoặc C:\\TNT\\license.key).")
        return 1

    ap = argparse.ArgumentParser(description="TNT Downloader (CLI)")
    ap.add_argument("inputs", nargs="+", help="link, hoặc đường dẫn file .txt chứa link")
    ap.add_argument("-o", "--out", default=os.path.join(os.path.expanduser("~"), "Downloads",
                                                        "TNT_Videos"), help="thư mục lưu")
    ap.add_argument("--jobs", type=int, default=3, help="số link tải song song (1-8)")
    ap.add_argument("--browser", default="",
                    help="nguồn cookies: tnt (hồ sơ trình duyệt của app) | chrome|edge|firefox|brave…")
    ap.add_argument("--cookies", default="", help="file cookies.txt (ưu tiên hơn --browser)")
    ap.add_argument("--browser-fallback", action="store_true",
                    help="cho phép mở trình duyệt bắt luồng khi mọi nguồn API thất bại "
                         "(mặc định TẮT — chạy im lặng, không chiếm máy)")
    ap.add_argument("--headless", action="store_true", help="ẩn cửa sổ trình duyệt khi bắt luồng")
    a = ap.parse_args()

    raw = []
    for x in a.inputs:
        if os.path.isfile(x):
            with open(x, "r", encoding="utf-8", errors="ignore") as f:
                raw.append(f.read())
        else:
            raw.append(x)
    urls = parse_links("\n".join(raw))
    if not urls:
        print("Không tìm thấy link nào.")
        return 1

    opts = Options(out_dir=a.out, browser=a.browser, concurrency=a.jobs,
                   browser_fallback=a.browser_fallback, headless=a.headless,
                   cookies_file=a.cookies)
    os.makedirs(a.out, exist_ok=True)
    print(f"Tải {len(urls)} link → {a.out}")
    res = download_many(urls, opts, on_log=lambda m: print(m))
    ok = sum(1 for r in res if r.ok)
    for i, r in enumerate(res, 1):
        print(f"{i:>3}. {'OK ' if r.ok else 'LỖI'} {r.title or r.url} "
              f"{'(' + human_size(r.size) + ', ' + r.via + ')' if r.ok else '- ' + r.error}")
    print(f"XONG: {ok} thành công · {len(res) - ok} lỗi")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
