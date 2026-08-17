"""
tnt_license.py — LỚP BẢO MẬT LICENSE DÙNG CHUNG cho mọi tool local của TNT.

=====================================================================
 ĐÂY LÀ FILE AN TOÀN ĐỂ PHÁT ĐI (đóng gói vào exe của từng tool).
 Nó chỉ chứa PUBLIC KEY — KHÔNG chứa private key/secret.
=====================================================================

Cách dùng trong một tool (chỉ 2 dòng ở đầu chương trình):

    from tnt_license import check_license
    check_license("TNT_Listing")      # tên tool này, phải khớp danh sách trong license

Nếu license hợp lệ → hàm trả về thông tin license và cho chạy tiếp.
Nếu KHÔNG hợp lệ → hiện thông báo (+ MÃ MÁY để gửi cho người cấp phép) rồi THOÁT.

Cơ chế:
  * Mỗi máy chỉ cần 1 file `license.key`.
  * File đó do người cấp phép ký bằng PRIVATE KEY (Ed25519). Ở đây ta chỉ có
    PUBLIC KEY để KIỂM chữ ký → không thể tự chế license giả.
  * License gắn với MÃ MÁY (machine fingerprint) → copy sang máy khác là hỏng.

Yêu cầu: thư viện `cryptography` (pip install cryptography).
"""
from __future__ import annotations

# Khi đóng gói PyInstaller, OpenSSL đôi khi báo "legacy provider failed to load".
# Ed25519/SHA-256/Fernet đều KHÔNG dùng legacy → tắt legacy cho an toàn. Phải đặt
# TRƯỚC khi cryptography được import (module này import cryptography kiểu lazy).
import os as _os
_os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

import base64
import binascii
import ctypes
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# ===================================================================== #
#  0. PUBLIC KEY  — DÁN CHUỖI HEX 64 KÝ TỰ DO make_keys.py IN RA VÀO ĐÂY.
#     (Đây là public key Ed25519 dạng raw, 32 byte = 64 hex.)
#     KHÔNG BAO GIỜ dán private key vào file này.
# ===================================================================== #
PUBLIC_KEY_HEX = "318a767a624f917ebf35b6a861ee1d36b935c526f90c499f9d1e170eddd96af1"

# ===================================================================== #
#  1. NƠI TÌM license.key
#     Thứ tự tìm: (1) cạnh tool đang chạy  →  (2) đường dẫn chung cố định.
#     ĐỔI ĐƯỜNG DẪN CHUNG Ở ĐÂY nếu muốn (hoặc đặt biến môi trường
#     TNT_LICENSE_PATH trỏ thẳng tới file license.key).
# ===================================================================== #
COMMON_LICENSE_PATH = r"C:\TNT\license.key"        # Windows: ổ C chung
COMMON_LICENSE_PATH_POSIX = "/Library/Application Support/TNT/license.key"  # macOS
LICENSE_FILENAME = "license.key"

# Sai số cho phép khi so ngày hết hạn (không dùng — để dành nếu cần).
# ===================================================================== #


class LicenseError(Exception):
    """Mọi lỗi liên quan license đều là LicenseError (thông báo an toàn, không lộ stack)."""


class LicenseInfo:
    """Thông tin trích ra từ một license hợp lệ."""

    def __init__(self, payload: dict, signature_hex: str):
        self.machine_id: str = payload.get("machine_id", "")
        self.expires: str | None = payload.get("expires")          # "YYYY-MM-DD" hoặc None
        self.tools: list[str] | None = payload.get("tools")        # None/[] = mọi tool
        self.issued: str | None = payload.get("issued")
        self.note: str = payload.get("note", "")
        self._payload = payload
        self._signature_hex = signature_hex

    def __repr__(self) -> str:
        return (f"<LicenseInfo note={self.note!r} expires={self.expires} "
                f"tools={self.tools}>")


# --------------------------------------------------------------------- #
#  2. MÃ MÁY (machine fingerprint)
# --------------------------------------------------------------------- #
def _run(cmd: list[str]) -> str:
    """Chạy lệnh hệ thống, trả stdout (đã strip). Ẩn cửa sổ console trên Windows."""
    try:
        kwargs = {}
        if os.name == "nt":
            # CREATE_NO_WINDOW = 0x08000000 → không nháy cửa sổ đen khi app windowed.
            kwargs["creationflags"] = 0x08000000
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL,
                                      timeout=10, **kwargs)
        return out.decode(errors="ignore").strip()
    except Exception:
        return ""


def _win_machine_guid() -> str:
    """MachineGuid trong registry — RẤT ổn định, luôn có trên Windows."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(val).strip()
    except Exception:
        return ""


def _win_board_uuid() -> str:
    """UUID mainboard/BIOS (Win32_ComputerSystemProduct). Thử PowerShell rồi wmic."""
    # PowerShell CIM (có mặt trên Win10/11).
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
    ])
    if out and "00000000-0000" not in out:
        return out.strip()
    # Dự phòng: wmic (một số máy vẫn còn).
    out = _run(["wmic", "csproduct", "get", "uuid"])
    for line in out.splitlines():
        line = line.strip()
        if line and line.lower() != "uuid" and "00000000-0000" not in line:
            return line
    return ""


def _win_volume_serial() -> str:
    """Serial của ổ đĩa hệ thống (volume serial) — đọc qua WinAPI, không cần lệnh ngoài."""
    try:
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        vol_serial = ctypes.c_uint(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive),
            None, 0,
            ctypes.byref(vol_serial),
            None, None, None, 0,
        )
        if ok:
            return f"{vol_serial.value:08X}"
    except Exception:
        pass
    return ""


# Đường dẫn TUYỆT ĐỐI tới ioreg. Khi app mở bằng double-click (Finder/launchd),
# PATH bị rút gọn và KHÔNG có /usr/sbin → gọi "ioreg" trần sẽ không tìm thấy →
# mã máy tính ra KHÁC so với lúc chạy trong Terminal → license không khớp.
# Dùng đường dẫn tuyệt đối để mã máy GIỐNG HỆT nhau ở mọi cách mở.
_IOREG = "/usr/sbin/ioreg"


def _ioreg_device() -> str:
    """Chạy ioreg (thử tuyệt đối trước, rồi PATH) — trả nguyên output."""
    for cmd0 in (_IOREG, "ioreg"):
        out = _run([cmd0, "-rd1", "-c", "IOPlatformExpertDevice"])
        if out:
            return out
    return ""


def _ioreg_value(key: str) -> str:
    for line in _ioreg_device().splitlines():
        if key in line:
            # dạng:  "IOPlatformUUID" = "XXXX-...."
            parts = line.split('"')
            if len(parts) >= 4:
                return parts[3].strip()
    return ""


def _mac_hw_uuid() -> str:
    """IOPlatformUUID trên macOS — định danh phần cứng ổn định."""
    return _ioreg_value("IOPlatformUUID")


def _mac_serial() -> str:
    return _ioreg_value("IOPlatformSerialNumber")


def _linux_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text().strip()
            if v:
                return v
        except Exception:
            pass
    return ""


def _linux_product_uuid() -> str:
    try:
        return Path("/sys/class/dmi/id/product_uuid").read_text().strip()
    except Exception:
        return ""


def _raw_sources() -> list[str]:
    """
    Danh sách nguồn phần cứng theo THỨ TỰ CỐ ĐỊNH. Nguồn không đọc được = "".
    Vì cùng một máy luôn chạy cùng đoạn code này (lúc lấy mã máy để xin license
    và lúc tool kiểm license), tập nguồn khả dụng là NHẤT QUÁN → mã máy ổn định.
    Ta tránh IP/tên máy (dễ đổi) và chỉ dùng định danh phần cứng bền.
    """
    if sys.platform.startswith("win"):
        return [
            "win-guid:" + _win_machine_guid(),
            "win-uuid:" + _win_board_uuid(),
            "win-vol:" + _win_volume_serial(),
        ]
    if sys.platform == "darwin":
        return [
            "mac-uuid:" + _mac_hw_uuid(),
            "mac-serial:" + _mac_serial(),
        ]
    return [
        "linux-mid:" + _linux_machine_id(),
        "linux-uuid:" + _linux_product_uuid(),
    ]


def get_machine_id() -> str:
    """
    Trả về MÃ MÁY: SHA-256 của các nguồn phần cứng, dạng hex 64 ký tự.
    Ổn định qua reboot; không đổi nếu phần cứng không đổi; giống nhau giữa
    bản chạy python và bản đóng gói exe.
    """
    sources = _raw_sources()
    # Nếu KHÔNG có nguồn phần cứng mạnh nào → không đủ an toàn để định danh.
    strong = [s for s in sources if s.split(":", 1)[1]]
    if not strong:
        raise LicenseError(
            "Không đọc được thông tin phần cứng để tạo mã máy trên hệ điều hành này."
        )
    joined = "|".join(sources)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest


def machine_id_pretty(mid: str | None = None) -> str:
    """Mã máy chia nhóm 8 ký tự cho dễ đọc/đọc-chép (chỉ để HIỂN THỊ)."""
    mid = mid or get_machine_id()
    return "-".join(mid[i:i + 8] for i in range(0, len(mid), 8)).upper()


# --------------------------------------------------------------------- #
#  3. ĐỌC & KIỂM license.key
# --------------------------------------------------------------------- #
def _app_dir() -> Path:
    """
    Thư mục để TÌM/GHI license.key & machine_id.txt (cạnh tool đang chạy).

    - Windows/Linux đóng gói: cạnh file thực thi.
    - macOS đóng gói .app: file thực thi nằm trong Foo.app/Contents/MacOS/, nên
      trả về thư mục CHỨA .app → người dùng đặt license.key CẠNH .app là đúng
      (trực quan, không phải chui vào trong bundle).
    - Chạy code: cạnh script.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for p in exe.parents:
            if p.suffix == ".app":
                return p.parent            # thư mục chứa Foo.app
        return exe.parent
    return Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("TNT_LICENSE_PATH")
    if env:
        paths.append(Path(env))
    paths.append(_app_dir() / LICENSE_FILENAME)
    if sys.platform.startswith("win"):
        paths.append(Path(COMMON_LICENSE_PATH))
    else:
        paths.append(Path(COMMON_LICENSE_PATH_POSIX))
    # loại trùng, giữ thứ tự
    seen, uniq = set(), []
    for p in paths:
        s = str(p).lower()
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def _find_license_file() -> Path | None:
    for p in _candidate_paths():
        try:
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def _canonical_payload_bytes(payload: dict) -> bytes:
    """Chuỗi JSON chuẩn hoá (sort key, không khoảng trắng) — phần được KÝ."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _load_public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not PUBLIC_KEY_HEX or PUBLIC_KEY_HEX == "PASTE_PUBLIC_KEY_HEX_HERE":
        raise LicenseError(
            "Chưa nhúng PUBLIC KEY vào tnt_license.py — hãy chạy make_keys.py và dán vào."
        )
    try:
        raw = binascii.unhexlify(PUBLIC_KEY_HEX.strip())
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as e:
        raise LicenseError(f"PUBLIC KEY không hợp lệ: {e}")


def encode_license(payload: dict, signature: bytes) -> str:
    """Đóng gói payload + chữ ký thành 1 chuỗi text (base64) để ghi ra license.key."""
    obj = {
        "payload": payload,
        "sig": binascii.hexlify(signature).decode(),
        "alg": "ed25519",
        "v": 1,
    }
    blob = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(blob).decode("ascii")


def _decode_license(text: str) -> tuple[dict, bytes]:
    try:
        blob = base64.b64decode(text.strip(), validate=True)
        obj = json.loads(blob.decode("utf-8"))
        payload = obj["payload"]
        sig = binascii.unhexlify(obj["sig"])
        if not isinstance(payload, dict):
            raise ValueError("payload sai kiểu")
        return payload, sig
    except Exception as e:
        raise LicenseError(f"File license hỏng hoặc sai định dạng ({e}).")


def verify_license_text(text: str, tool_name: str,
                        this_machine_id: str | None = None) -> LicenseInfo:
    """
    Kiểm một chuỗi license (đã đọc từ file). Ném LicenseError nếu bất kỳ phép kiểm nào sai.
    Trả về LicenseInfo nếu hợp lệ. (Tách riêng để dễ viết unit test.)
    """
    from cryptography.exceptions import InvalidSignature

    payload, signature = _decode_license(text)

    # (1) Chữ ký hợp lệ với public key?  → chống license giả.
    pub = _load_public_key()
    try:
        pub.verify(signature, _canonical_payload_bytes(payload))
    except InvalidSignature:
        raise LicenseError("Chữ ký license KHÔNG hợp lệ (license giả hoặc đã bị sửa).")

    info = LicenseInfo(payload, binascii.hexlify(signature).decode())

    # (2) Mã máy khớp?  → chống copy sang máy khác.
    this_id = this_machine_id or get_machine_id()
    if not info.machine_id or info.machine_id.lower() != this_id.lower():
        raise LicenseError("License này KHÔNG dành cho máy hiện tại (mã máy không khớp).")

    # (3) Còn hạn?
    if info.expires:
        try:
            exp = datetime.strptime(info.expires, "%Y-%m-%d").date()
        except ValueError:
            raise LicenseError("Ngày hết hạn trong license sai định dạng.")
        if date.today() > exp:
            raise LicenseError(f"License đã HẾT HẠN ngày {info.expires}.")

    # (4) Tool này có được phép? (danh sách rỗng/None = mọi tool)
    if info.tools:
        allowed = {t.strip().lower() for t in info.tools}
        if tool_name.strip().lower() not in allowed:
            raise LicenseError(
                f"License không cấp quyền cho tool '{tool_name}'. "
                f"Chỉ được dùng: {', '.join(info.tools)}."
            )

    return info


# --------------------------------------------------------------------- #
#  4. HIỂN THỊ THÔNG BÁO (an toàn cho cả app windowed lẫn console)
# --------------------------------------------------------------------- #
def _write_machine_id_file(mid_pretty: str) -> Path | None:
    """Ghi mã máy ra file cạnh tool để user dễ COPY (app windowed không có console)."""
    try:
        out = _app_dir() / "machine_id.txt"
        out.write_text(
            "MA MAY (gui cho nguoi cap phep de xin license):\n\n"
            + mid_pretty + "\n",
            encoding="utf-8",
        )
        return out
    except Exception:
        return None


def _show_message(title: str, message: str) -> None:
    """Hiện thông báo: ưu tiên hộp thoại Qt → tkinter → in ra console."""
    # Thử Qt (các tool này đều dùng PySide6).
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()
        return
    except Exception:
        pass
    # Thử tkinter.
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
        return
    except Exception:
        pass
    # Cuối cùng: in ra (nếu có console).
    print("\n" + "=" * 60 + f"\n{title}\n" + "-" * 60 + f"\n{message}\n"
          + "=" * 60, file=sys.stderr)


def _qt_machine_id_dialog(title: str, reason: str, tool_name: str,
                          mid_pretty: str, mid_file: "Path | None") -> bool:
    """
    Hộp thoại Qt hiện MÃ MÁY + nút 'Copy mã máy' (giúp user lấy mã NHANH).
    Trả True nếu hiện được bằng Qt, False nếu không có Qt (để fallback).
    """
    try:
        from PySide6.QtWidgets import (
            QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton,
        )
        from PySide6.QtGui import QGuiApplication, QFont
        from PySide6.QtCore import Qt
    except Exception:
        return False
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        dlg = QDialog()
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel(f"<b>{reason}</b>"))
        if tool_name:
            lay.addWidget(QLabel(f"Tool: {tool_name}"))
        lay.addWidget(QLabel("MÃ MÁY của bạn — gửi cho người quản trị để xin license:"))

        ed = QLineEdit(mid_pretty)
        ed.setReadOnly(True)
        f = QFont("Consolas"); f.setBold(True); ed.setFont(f)
        ed.setCursorPosition(0)
        lay.addWidget(ed)

        row = QHBoxLayout()
        btn_copy = QPushButton("📋  Copy mã máy")

        def _do_copy():
            QGuiApplication.clipboard().setText(mid_pretty)
            btn_copy.setText("✓ Đã copy!")
        btn_copy.clicked.connect(_do_copy)
        btn_close = QPushButton("Thoát")
        btn_close.clicked.connect(dlg.accept)
        row.addWidget(btn_copy)
        row.addStretch(1)
        row.addWidget(btn_close)
        lay.addLayout(row)

        note = (f"Sau khi nhận file '{LICENSE_FILENAME}', đặt cạnh chương trình "
                f"hoặc tại {COMMON_LICENSE_PATH}.")
        if mid_file:
            note += f"\n(Mã máy cũng đã lưu vào: {mid_file})"
        lbl = QLabel(note); lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#666; font-size:11px;")
        lay.addWidget(lbl)

        dlg.exec()
        return True
    except Exception:
        return False


def show_machine_id(reason: str = "MÃ MÁY", tool_name: str = "") -> str:
    """
    Hiện MÃ MÁY (có nút Copy) và ghi machine_id.txt. Trả về mã máy (bản đẹp).
    Dùng cho: (a) khi thiếu license, (b) tool nhỏ 'Lấy mã máy'.
    """
    try:
        mid = machine_id_pretty()
    except Exception:
        mid = "(không đọc được thông tin phần cứng)"
    mid_file = _write_machine_id_file(mid)
    if not _qt_machine_id_dialog("Mã máy", reason, tool_name, mid, mid_file):
        # Fallback không có Qt.
        _show_message("Mã máy",
                      f"{reason}\n\nMÃ MÁY:\n{mid}\n\n(Đã lưu machine_id.txt)")
    return mid


def _deny_and_exit(reason: str, tool_name: str) -> "None":
    """Báo lỗi rõ ràng + hiện mã máy (có nút Copy), rồi THOÁT. Không lộ stack trace."""
    show_machine_id(reason, tool_name)
    sys.exit(1)


# --------------------------------------------------------------------- #
#  5. HÀM CHÍNH — gọi ở đầu MỌI tool
# --------------------------------------------------------------------- #
def check_license(tool_name: str, *, raise_on_error: bool = False) -> LicenseInfo:
    """
    Kiểm license cho `tool_name`. Gọi ở DÒNG ĐẦU khi tool khởi động.

    - Hợp lệ  → trả về LicenseInfo (chứa expires, tools, note...).
    - Sai/thiếu → hiện thông báo + mã máy rồi sys.exit(1)
                  (đặt raise_on_error=True để ném LicenseError thay vì thoát —
                   hữu ích khi tự viết UI xử lý riêng).
    """
    try:
        lic_path = _find_license_file()
        if lic_path is None:
            raise LicenseError(
                f"Không tìm thấy file '{LICENSE_FILENAME}'. "
                f"Máy này CHƯA được cấp phép."
            )
        text = lic_path.read_text(encoding="utf-8", errors="ignore")
        info = verify_license_text(text, tool_name)
        return info
    except LicenseError as e:
        if raise_on_error:
            raise
        _deny_and_exit(str(e), tool_name)
    except Exception as e:  # phòng lỗi bất ngờ — vẫn từ chối an toàn.
        if raise_on_error:
            raise LicenseError(f"Lỗi kiểm license: {e}")
        _deny_and_exit(f"Lỗi kiểm license: {e}", tool_name)


# --------------------------------------------------------------------- #
#  6. (TUỲ CHỌN — BẢO MẬT MẠNH) Phái sinh khoá từ license để MÃ HOÁ dữ liệu lõi.
#     Dùng khi muốn "thiếu license hợp lệ thì phần lõi KHÔNG giải mã được",
#     thay vì chỉ chặn bằng một câu if. Xem README mục 5.
# --------------------------------------------------------------------- #
def derive_fernet_key(info: LicenseInfo) -> bytes:
    """
    Sinh khoá Fernet (32 byte urlsafe-base64) gắn CHẶT với license + máy này.
    Chỉ tạo được đúng khoá khi có license hợp lệ trên đúng máy.
    """
    material = (info.machine_id + "|" + info._signature_hex).encode("utf-8")
    raw = hashlib.sha256(material).digest()          # 32 byte
    return base64.urlsafe_b64encode(raw)


def unlock_secret(info: LicenseInfo, encrypted_blob: bytes) -> bytes:
    """Giải mã một blob đã mã hoá bằng derive_fernet_key(). Ném lỗi nếu sai khoá."""
    from cryptography.fernet import Fernet
    return Fernet(derive_fernet_key(info)).decrypt(encrypted_blob)


# --------------------------------------------------------------------- #
#  Chạy trực tiếp `python tnt_license.py`  → in mã máy (tiện để test).
# --------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        print("MÃ MÁY (raw) :", get_machine_id())
        print("MÃ MÁY (đẹp) :", machine_id_pretty())
    except LicenseError as e:
        print("Lỗi:", e)
