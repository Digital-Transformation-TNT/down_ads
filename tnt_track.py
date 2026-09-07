"""tnt_track.py — GHI NHẬT KÝ SỬ DỤNG cho các tool desktop của TNT.

Bản song sinh của `frontend/src/lib/track.js` bên webtool. Cùng bắn về một chỗ
(`POST /api/events`), cùng một schema, nên tab "Quản lý thời lượng" gộp được số
liệu của bản web và bản app vào chung một báo cáo mà KHÔNG phải sửa ETL.

=====================================================================
 KHÔNG BAO GIỜ ĐƯỢC LÀM VỠ APP.
 Mọi lỗi trong file này đều bị nuốt. Mất log còn hơn hỏng việc của người dùng.
=====================================================================

CHỈ DÙNG THƯ VIỆN CHUẨN. `requests` chỉ có sẵn ở down_ads, hai app kia không có
-> dùng `urllib` để cả ba cài đặt như nhau, không phải thêm dependency.

KHÁC BIỆT DUY NHẤT SO VỚI BẢN WEB — và vì sao:
    Bên web, job chạy trên máy chủ nên BACKEND ghi dòng `job_*` kèm
    `latency_ms`; frontend bị cấm ghi để khỏi đếm đôi. Ở app desktop, job chạy
    ngay trong tiến trình này, máy chủ không hề biết -> chính app phải ghi. Vẫn
    giữ đúng nguyên tắc "hai chiếc đồng hồ": `latency_ms` chỉ đo lúc máy CHẠY
    THẬT, không tính lúc người ngồi nghĩ.

BỐN TẦNG ĐỊNH DANH (y hệt bản web): user_id > session_id > task_id > step.
    session_id: một lần dùng app; im lặng quá 30' thì mở phiên mới.
    task_id   : một lần làm ra một sản phẩm. Chạy lại KHÔNG mở task mới,
                chỉ tăng `attempt`.

CÁCH DÙNG:

    from tnt_license import check_license
    import tnt_track

    info = check_license("TNT_Downloader")      # giữ lại giá trị trả về
    tnt_track.init("down_ads", license_info=info)

    tnt_track.feature_open()
    tnt_track.step("paste_links", "prepare", {"count": 12})
    tnt_track.run_click({"phase": "download"})
    with tnt_track.job("download", count=12):
        ...                                     # máy chạy
    tnt_track.output("mp4", {"count": 12})
    tnt_track.shutdown()                        # trong closeEvent
"""

from __future__ import annotations

import json
import os
import platform
import random
import string
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# ── Cấu hình ─────────────────────────────────────────────────────
#
# Đổi nhanh bằng biến môi trường, không phải build lại app (cùng lối nghĩ với
# TNT_SOURCES_URL trong down_ads/core/sources.py).

DEFAULT_ENDPOINT = "https://tntgroup-website-content.hf.space/api/events"

ENDPOINT = os.environ.get("TNT_TRACK_URL", "").strip() or DEFAULT_ENDPOINT
DISABLED = os.environ.get("TNT_TRACK_OFF", "").strip() not in ("", "0", "false", "False")

FLUSH_SEC = 5.0                  # nhịp gửi lô
MAX_BATCH = 100                  # trần server là 200; để 100 cho thoáng
IDLE_SEC = 30 * 60               # im lặng quá 30' -> phiên mới (khớp SESSION_IDLE_MINUTES)
HTTP_TIMEOUT = 10.0
QUEUE_MAX = 5000                 # trần hàng đợi lưu đĩa, tránh phình vô hạn

STEPS = ("open", "prepare", "run", "review", "output", "done")


# ── Nơi lưu trạng thái & hàng đợi ────────────────────────────────

def _state_dir() -> Path:
    """Thư mục ~/.tnt_track — nằm NGOÀI thư mục app nên build lại app vẫn giữ phiên."""
    try:
        d = Path(os.environ.get("TNT_TRACK_DIR") or (Path.home() / ".tnt_track"))
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return Path(".")


def _state_path() -> Path:
    return _state_dir() / "state.json"


def _queue_path() -> Path:
    return _state_dir() / "queue.jsonl"


# ── Tiện ích ─────────────────────────────────────────────────────

def _iso_now() -> str:
    """ISO8601 kèm múi giờ máy + mili giây — đúng định dạng bản web gửi lên."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _rand(n: int = 4) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def _id_safe(x: str) -> str:
    """Mảnh định danh an toàn cho ID: bỏ dấu cách/ký tự lạ, cắt ngắn."""
    out = "".join(c for c in (x or "").lower() if c.isalnum())
    return out[:12] or "anon"


def _mk_id(prefix: str, user: str) -> str:
    """<prefix>_<user>_<YYYYMMDD>_<HHMMSS>_<rand4> — y hệt quy ước bản web.

    Dùng giờ + ngẫu nhiên thay số thứ tự -> mở hai app cùng lúc không đụng nhau.
    """
    d = datetime.now()
    return "%s_%s_%s_%s_%s" % (prefix, _id_safe(user), d.strftime("%Y%m%d"),
                               d.strftime("%H%M%S"), _rand())


def _client_name() -> str:
    """Nhãn thiết bị để dashboard tách được web / Windows / Mac."""
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _norm_user(raw: str) -> str:
    """Chuẩn hoá tên người dùng cho khớp quy ước webtool ("Tên - Team - BU").

    Ô "Nhân viên" của bộ cấp license gợi ý dấu gạch dài, còn webtool tách tên
    bằng " - ". Quy về một dạng để cùng một người không thành hai dòng trong
    báo cáo. Backend còn lowercase lần nữa, nhưng làm sẵn ở đây cho chắc.
    """
    s = (raw or "").strip()
    for dash in ("—", "–", "‒"):
        s = s.replace(dash, "-")
    parts = [p.strip() for p in s.split("-")]
    s = " - ".join(p for p in parts if p)
    return " ".join(s.split()).lower()[:120]


# ── Trạng thái (một app một tiến trình -> singleton là đủ) ────────

class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.user = ""
        self.feature = ""
        self.session = ""
        self.task = None          # dict: id/feature/started/last_step/closed/attempt/phase
        self.queue = []
        self.extra = {}           # gắn kèm dòng session_start
        self.started = False
        self.sender = None
        self.stop = threading.Event()
        self.inited = False


_S = _State()


# ── Đọc/ghi trạng thái phiên ─────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        _state_path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _session_id() -> str:
    """Phiên cũ nếu vừa dùng trong 30', không thì mở phiên mới.

    Trạng thái nằm trên đĩa nên đóng app rồi mở lại trong vòng 30 phút vẫn tính
    là MỘT phiên — giống hệt bản web giữ session_id trong localStorage.
    """
    with _S.lock:
        if _S.session:
            return _S.session
        st = _load_state()
        try:
            last = float(st.get("last_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        prev = st.get("session") or ""
        if prev and last and (time.time() - last) < IDLE_SEC:
            _S.session = prev
            _S.started = st.get("started_for") == prev
        else:
            _S.session = _mk_id("s", _S.user)
            _S.started = False
        return _S.session


def _touch() -> None:
    try:
        st = _load_state()
        st["session"] = _S.session
        st["last_ts"] = time.time()
        if _S.started:
            st["started_for"] = _S.session
        _save_state(st)
    except Exception:
        pass


# ── Hàng đợi: bộ nhớ + đĩa ───────────────────────────────────────
#
# Máy để bàn thì mạng ổn, nhưng laptop rớt wifi / ngủ / đổi mạng là chuyện
# thường. Gửi hỏng -> đẩy xuống đĩa, lần sau gửi lại. Bản web hiện xoá hàng đợi
# TRƯỚC khi gửi nên hỏng là mất hẳn; ở đây không lặp lại lỗi đó.

def _disk_load() -> list:
    p = _queue_path()
    try:
        if not p.exists():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        try:
            p.unlink()
        except OSError:
            pass
        return rows[-QUEUE_MAX:]
    except Exception:
        return []


def _disk_save(rows: list) -> None:
    if not rows:
        return
    try:
        with _queue_path().open("a", encoding="utf-8") as f:
            for r in rows[-QUEUE_MAX:]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _post(rows: list) -> bool:
    """Gửi một lô. True = server đã nhận.

    `sent_at` = giờ máy này lúc GỬI. Máy chủ lấy nó làm mốc chỉnh lệch đồng hồ
    thay vì lấy event mới nhất, nhờ vậy lô gửi bù sau khi mất mạng vẫn giữ
    đúng NGÀY xảy ra thật, không bị dồn hết về lúc gửi.
    """
    try:
        body = json.dumps({"events": rows, "sent_at": _iso_now()},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "tnt-track/1.0 (%s)" % _client_name()},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _take(n: int) -> list:
    with _S.lock:
        out, _S.queue = _S.queue[:n], _S.queue[n:]
        return out


def _sender_loop() -> None:
    """Luồng nền: cứ FLUSH_SEC giây gửi một lô. Không bao giờ đụng vào giao diện."""
    # Log tồn từ lần chạy trước (mất mạng / tắt máy đột ngột) -> gửi bù trước.
    try:
        old = _disk_load()
        if old:
            with _S.lock:
                _S.queue = old + _S.queue
    except Exception:
        pass
    while not _S.stop.wait(FLUSH_SEC):
        try:
            rows = _take(MAX_BATCH)
            if not rows:
                continue
            if not _post(rows):
                _disk_save(rows)      # để dành, vòng sau thử lại
        except Exception:
            pass


def flush() -> None:
    """Đẩy hết hàng đợi đi NGAY (chặn). Chỉ dùng lúc thoát app."""
    if DISABLED:
        return
    try:
        while True:
            rows = _take(MAX_BATCH)
            if not rows:
                break
            if not _post(rows):
                _disk_save(rows)
                _disk_save(_take(QUEUE_MAX))
                break
    except Exception:
        pass


# ── Ghi một dòng ─────────────────────────────────────────────────

def _push(row: dict) -> None:
    if DISABLED or not _S.inited:
        return
    try:
        with _S.lock:
            if not _S.user:
                return                       # không quy được về ai -> bỏ
            row.setdefault("event_id", "evt_" + uuid.uuid4().hex[:16])
            row.setdefault("event_time", _iso_now())
            row["user_id"] = _S.user
            _S.queue.append(row)
            if len(_S.queue) > QUEUE_MAX:
                _S.queue = _S.queue[-QUEUE_MAX:]
        _touch()
    except Exception:
        pass


def _emit(event_name: str, step_name: str = "prepare", props: dict = None,
          value: str = "", latency_ms: int = None) -> None:
    try:
        t = _S.task
        p = dict(props or {})
        p.setdefault("client", _client_name())
        _push({
            "session_id": _session_id(),
            "task_id": t["id"] if t else "",
            "feature": (t["feature"] if t else _S.feature) or "",
            "step": step_name if step_name in STEPS else "prepare",
            "event_name": event_name,
            "event_value": "" if value is None else str(value),
            "latency_ms": latency_ms,
            "properties": p,
        })
        if t and step_name in STEPS and step_name != "done":
            t["last_step"] = step_name
    except Exception:
        pass


# ── API công khai ────────────────────────────────────────────────

def init(feature: str, license_info=None, user: str = "", **extra) -> None:
    """Gọi MỘT LẦN lúc app khởi động, sau check_license().

    `license_info`: giá trị trả về của check_license(). CHỈ ĐỌC — lấy tên nhân
    viên đã ký sẵn ở trường `note` làm định danh, và mã máy làm đường lùi khi
    license không ghi tên. Không đụng gì tới cơ chế license.
    """
    if DISABLED:
        return
    try:
        with _S.lock:
            if _S.inited:
                return
            _S.feature = feature or ""

            # Thứ tự ưu tiên: chỉ định tay > biến môi trường > tên trong
            # license > mã máy. Luôn có giá trị nên log không bao giờ mồ côi.
            uid = _norm_user(user) or _norm_user(os.environ.get("TNT_TRACK_USER", ""))
            note = ""
            machine = ""
            if license_info is not None:
                note = getattr(license_info, "note", "") or ""
                machine = getattr(license_info, "machine_id", "") or ""
            if not uid:
                uid = _norm_user(note)
            if not uid and machine:
                uid = "m_" + machine[:12].lower()
            _S.user = uid
            _S.extra = {
                "client": _client_name(),
                "os": ("%s %s" % (platform.system(), platform.release()))[:60],
                "app": feature or "",
            }
            if note:
                _S.extra["license_note"] = note[:120]
            if machine:
                _S.extra["machine"] = machine[:16]
            for k, v in (extra or {}).items():
                if v is not None:
                    _S.extra[str(k)[:30]] = v
            _S.inited = True

            _S.sender = threading.Thread(target=_sender_loop, daemon=True,
                                         name="tnt-track")
            _S.sender.start()
        session_start()
    except Exception:
        pass


def session_start(**props) -> None:
    """Mở app. Tự chống ghi trùng khi phiên cũ đã có dòng này rồi."""
    try:
        _session_id()
        with _S.lock:
            if _S.started:
                return
            _S.started = True
        _push({
            "session_id": _S.session, "task_id": "", "feature": "", "step": "open",
            "event_name": "session_start", "event_value": "",
            "latency_ms": None,
            "properties": dict(_S.extra, **(props or {})),
        })
        _touch()
    except Exception:
        pass


def feature_open(feature: str = "", **props) -> None:
    """Bắt đầu MỘT VIỆC mới. Việc đang dở (nếu có) bị đóng lại là 'bỏ dở'."""
    try:
        f = feature or _S.feature
        if not f:
            return
        abandon()                       # chốt việc cũ trước khi mở việc mới
        with _S.lock:
            _S.task = {"id": _mk_id(_id_safe(f), _S.user), "feature": f,
                       "started": time.time(), "last_step": "open",
                       "closed": False, "attempt": 0, "phase": ""}
        _emit("feature_open", "open", props)
    except Exception:
        pass


def step(event_name: str, step_name: str = "prepare", props: dict = None,
         value: str = "") -> None:
    """Một thao tác thường của người (dán link, chọn file, duyệt kết quả...)."""
    try:
        if not _S.task:
            return
        if _S.task.get("closed"):
            feature_open(_S.task["feature"], source="continue")
        _emit(event_name, step_name, props, value)
    except Exception:
        pass


def run_click(props: dict = None) -> int:
    """Người bấm nút chạy. Tăng `attempt` — đây là chỉ số A4."""
    try:
        if not _S.task:
            feature_open()
        if _S.task and _S.task.get("closed"):
            feature_open(_S.task["feature"], source="continue")
        if not _S.task:
            return 0
        p = dict(props or {})
        with _S.lock:
            _S.task["attempt"] += 1
            if p.get("phase"):
                _S.task["phase"] = str(p["phase"])
            n = _S.task["attempt"]
        _emit("run_click", "run", dict(p, attempt=n))
        return n
    except Exception:
        return 0


def retry(event_name: str = "regenerate", props: dict = None) -> None:
    """Bấm làm lại — CÙNG một việc, chỉ tăng số lần bấm."""
    try:
        if not _S.task:
            return
        with _S.lock:
            _S.task["attempt"] += 1
            n = _S.task["attempt"]
        _emit(event_name, "run", dict(props or {}, attempt=n))
    except Exception:
        pass


def job_started(phase: str = "", **props) -> None:
    p = dict(props)
    p["phase"] = phase or (_S.task or {}).get("phase", "")
    _emit("job_started", "run", p)


def job_done(phase: str = "", latency_ms: int = None, ok: bool = True,
             **props) -> None:
    """Máy chạy xong. `latency_ms` = CHẠY THẬT, không tính lúc người ngồi chờ."""
    p = dict(props)
    p["phase"] = phase or (_S.task or {}).get("phase", "")
    if _S.task:
        p.setdefault("attempt", _S.task.get("attempt") or 1)
    _emit("job_done" if ok else "job_failed", "run", p,
          value="done" if ok else "error",
          latency_ms=int(latency_ms) if latency_ms and latency_ms > 0 else None)


@contextmanager
def job(phase: str = "", **props):
    """Bọc đoạn MÁY CHẠY. Tự ghi job_started -> job_done kèm thời gian chạy.

        with tnt_track.job("download", count=12):
            ...

    Lỗi giữa chừng vẫn ghi `job_failed` rồi ném tiếp — tracking không được nuốt
    lỗi của nghiệp vụ.
    """
    t0 = time.time()
    job_started(phase, **props)
    try:
        yield
    except BaseException as ex:
        job_done(phase, int((time.time() - t0) * 1000), ok=False,
                 error=type(ex).__name__, **props)
        raise
    else:
        job_done(phase, int((time.time() - t0) * 1000), ok=True, **props)


def output(kind: str = "file", props: dict = None, forward_to: str = "") -> None:
    """CÓ SẢN PHẨM — chốt việc là XONG. Đây là chỉ số B1 và tử số của A3.

    `props["count"]` = số file làm ra (mặc định 1).
    """
    try:
        if not _S.task:
            return
        fwd = bool(forward_to)
        p = dict(props or {})
        p["success_type"] = "forward" if fwd else "download"
        if fwd:
            p["to"] = forward_to
        _emit("output_forward" if fwd else "output_download", "output", p,
              value=forward_to if fwd else kind)
        with _S.lock:
            t = _S.task
            if t.get("closed"):
                return
            t["closed"] = True
        _emit("feature_complete", "done", {
            "success_type": "forward" if fwd else "download",
            "dur_ms": int((time.time() - t["started"]) * 1000),
            "attempts": t.get("attempt") or 0,
        })
    except Exception:
        pass


def thumbs(value: str, props: dict = None) -> None:
    """Nút 👍/👎 ở màn kết quả — chỉ số B3."""
    try:
        if not _S.task:
            return
        _emit("thumbs_feedback",
              "output" if _S.task.get("closed") else "review",
              props, "up" if value == "up" else "down")
    except Exception:
        pass


def restart(**props) -> None:
    """Nút 'Làm mới / Bắt đầu lại' — đóng việc cũ, mở việc mới cùng tool."""
    try:
        if not _S.task:
            return
        f, parent = _S.task["feature"], _S.task["id"]
        feature_open(f, source="rerun", parent_task=parent, **props)
    except Exception:
        pass


def abandon(reason: str = "", last_step: str = "") -> None:
    """Chốt việc đang dở là BỎ DỞ. Ghi lại dừng ở bước nào — đó là chỉ số A2."""
    try:
        with _S.lock:
            t = _S.task
            if not t or t.get("closed"):
                return
            if last_step:
                t["last_step"] = last_step
            t["closed"] = True
        p = {"last_step": t.get("last_step") or "open",
             "dur_ms": int((time.time() - t["started"]) * 1000),
             "attempts": t.get("attempt") or 0}
        if reason:
            p["reason"] = reason
        _emit("feature_abandon", t.get("last_step") or "open", p)
    except Exception:
        pass


def shutdown(reason: str = "close_app") -> None:
    """Gọi trong closeEvent. Chốt việc dở rồi cố gửi nốt trước khi thoát.

    App desktop đóng là tiến trình chết hẳn — không có `pagehide` như trình
    duyệt để cứu. Không gọi hàm này thì dòng `feature_abandon` mất, và phễu
    "Điểm dừng / bỏ dở" trên dashboard rỗng dần.
    """
    try:
        abandon(reason)
        _S.stop.set()
        flush()
        with _S.lock:
            if _S.queue:
                _disk_save(_S.queue)     # gửi không kịp -> để lần mở sau gửi bù
                _S.queue = []
    except Exception:
        pass


def current_ctx() -> dict:
    """Ngữ cảnh hiện tại — tiện khi cần gắn tay vào chỗ khác."""
    t = _S.task or {}
    return {"user_id": _S.user, "session_id": _S.session,
            "task_id": t.get("id", ""), "feature": t.get("feature", _S.feature),
            "phase": t.get("phase", ""), "attempt": t.get("attempt", 0)}
