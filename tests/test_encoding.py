"""Chặn tái diễn lỗi: gọi ffmpeg/ffprobe mà quên khai báo encoding.

BỐI CẢNH — lỗi này đã xảy ra thật và làm hỏng tool cho người dùng:

    subprocess.run([...], capture_output=True, text=True)   # THIẾU encoding

`text=True` không kèm `encoding` thì Python giải mã output theo BẢNG MÃ HỆ
THỐNG — trên Windows tiếng Việt là cp1252. Mà ffmpeg in LẠI TÊN FILE vào
output, còn tên file thì đặt theo tiêu đề video: tiêu đề có chữ Hán hoặc
tiếng Việt là ném UnicodeDecodeError.

Lỗi đó rơi vào `except Exception: return False`, nên hàm kiểm tra kết luận
"file không phải video" -> tool VỨT BỎ file vừa tải xong và chuyển sang nguồn
khác, cho tới khi hết nguồn thì báo lỗi. Người dùng thấy tải chạy bình thường
rồi tự nhiên hỏng, thử nguồn nào cũng hỏng — vì hỏng nằm ở khâu KIỂM chứ
không phải khâu tải.

VÌ SAO KIỂM BẰNG CÁCH ĐỌC MÃ NGUỒN, KHÔNG PHẢI CHẠY THỬ:
    Lỗi chỉ nổ khi bảng mã hệ thống KHÔNG phải UTF-8. CI chạy Linux (mặc định
    UTF-8) nên chạy thử bao nhiêu lần cũng xanh, không bắt được gì. Đọc thẳng
    mã nguồn thì bắt được trên mọi hệ điều hành, chạy trong vài mili giây và
    không cần ffmpeg.

Chạy:  python tests/test_encoding.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Thư mục không phải mã của mình -> khỏi quét.
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__",
             "node_modules", "vendor", "tests"}


def _py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(base, fn)


def check_source() -> list[str]:
    """Tìm mọi lời gọi subprocess có text=True mà thiếu encoding.

    Bắt cả `text=True` lẫn `universal_newlines=True` (bí danh cũ của nó).
    Quét theo TỪNG LỜI GỌI chứ không theo từng dòng, vì tham số hay được
    xuống dòng cho gọn -> soi một dòng sẽ báo nhầm.
    """
    bad = []
    call = re.compile(r"subprocess\.(?:run|check_output|Popen)\s*\(", re.S)
    for path in _py_files():
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        for m in call.finditer(src):
            # Cắt lấy đúng phần trong ngoặc của lời gọi này.
            i, depth = m.end(), 1
            while i < len(src) and depth:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            args = src[m.end():i]
            if "text=True" not in args and "universal_newlines=True" not in args:
                continue                      # trả bytes -> không có gì để giải mã
            if "encoding=" in args:
                continue                      # đã khai báo -> đạt
            line = src[:m.start()].count("\n") + 1
            bad.append("%s:%d" % (os.path.relpath(path, ROOT), line))
    return bad


def check_runtime() -> str:
    """Chạy thật: tên file có dấu thì đọc output ffmpeg có vỡ không.

    Không có ffmpeg thì bỏ qua — phần đọc mã nguồn ở trên mới là chốt chặn.
    """
    ff = None
    for cand in ("ffmpeg", "ffmpeg.exe"):
        try:
            subprocess.run([cand, "-version"], capture_output=True, timeout=20)
            ff = cand
            break
        except Exception:
            continue
    if not ff:
        return "bỏ qua (máy không có ffmpeg)"

    tmp = tempfile.mkdtemp()
    # Tên file cố ý có dấu tiếng Việt + chữ Hán — đúng thứ làm cp1252 vỡ.
    out = os.path.join(tmp, "Cà phê 微微辣.mp4")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=64x64:rate=5", "-t", "1", out],
                   capture_output=True, timeout=60)
    if not os.path.exists(out):
        return "bỏ qua (không dựng được video mẫu)"

    p = subprocess.run([ff, "-hide_banner", "-i", out],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if "Video:" not in (p.stderr or ""):
        raise AssertionError(
            "Đọc output ffmpeg cho file tên có dấu KHÔNG thấy luồng video — "
            "đúng triệu chứng của lỗi đã sửa.")
    return "đạt (tên file có dấu vẫn đọc được luồng video)"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    bad = check_source()
    if bad:
        print("THIẾU encoding ở %d lời gọi subprocess:" % len(bad))
        for b in bad:
            print("   ", b)
        print()
        print('Sửa: thêm  encoding="utf-8", errors="replace"  vào các lời gọi trên.')
        print("Vì sao: xem phần đầu file này.")
        return 1
    print("[1/2] Đọc mã nguồn: đạt — mọi lời gọi subprocess đều khai báo encoding.")

    try:
        print("[2/2] Chạy thật:", check_runtime())
    except AssertionError as e:
        print("[2/2] Chạy thật: HỎNG —", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
