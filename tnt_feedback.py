"""tnt_feedback.py — thanh 👍/👎 nhỏ, dùng chung cho các tool desktop của TNT.

Đây là nguồn duy nhất của chỉ số B3 ("Hài lòng") trên tab "Quản lý thời lượng":
không có ai bấm thì cột đó hiện dấu "—", chứ không có cách nào suy ra từ log.

=====================================================================
 NGUYÊN TẮC: HỎI CHO KHÉO, KHÔNG LÀM PHIỀN.
=====================================================================

Người dùng mở tool để LÀM VIỆC, không phải để chấm điểm. Một thanh hỏi ý kiến
đặt sai chỗ còn hại hơn là không có số liệu. Nên widget này:

    1. NẰM IM cho tới khi thật sự có kết quả. Không chiếm chỗ, không hiện lúc
       đang chạy, không hiện lúc vừa mới mở app.
    2. KHÔNG HỎI KHI VỪA LỖI. Hỏi "có hài lòng không?" ngay sau khi việc hỏng
       vừa vô duyên vừa cho ra số liệu rác — người ta bực cái lỗi, không phải
       bực chất lượng kết quả. Nơi gọi tự quyết định bằng cách chỉ gọi `ask()`
       khi có sản phẩm thật.
    3. MỘT DÒNG MỎNG, không viền, không màu nền — thừa hưởng bảng màu của app
       nên nhìn như vốn có sẵn, không như miếng vá dán thêm.
    4. BẤM MỘT LẦN LÀ XONG. Đổi thành lời cảm ơn rồi tự biến mất. Không hộp
       thoại, không chặn thao tác, không hỏi thêm câu nào.
    5. KHÔNG ĐEO BÁM. Bỏ qua thì tự ẩn sau `AUTO_HIDE_SEC` giây. Không trả lời
       cũng là một câu trả lời — và im lặng thì đừng ghi gì cả, đừng đoán.
    6. MỖI VIỆC HỎI ĐÚNG MỘT LẦN. Chạy mẻ mới -> `reset()` -> mới hỏi lại.

Ghi nhận qua `tnt_track.thumbs()`, cùng đường với mọi log khác.

CÁCH DÙNG:

    from tnt_feedback import FeedbackBar

    self.fb = FeedbackBar(accent=ORANGE)      # lúc dựng giao diện
    layout.addWidget(self.fb)

    self.fb.reset()                           # lúc bắt đầu mẻ mới
    self.fb.ask()                             # CHỈ khi mẻ ra sản phẩm
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

try:
    import tnt_track
except Exception:                                    # thiếu module -> vẫn chạy
    tnt_track = None

# Bỏ qua ngần này giây thì tự ẩn (đừng để nó nằm lì trên giao diện).
AUTO_HIDE_SEC = 25

# Cảm ơn xong bao lâu thì biến mất.
THANKS_SEC = 2.0

_QUESTION = "Kết quả có dùng được không?"
_THANKS = "Cảm ơn bạn đã góp ý."


class FeedbackBar(QWidget):
    """Một dòng: câu hỏi + 👍 + 👎. Mặc định ẩn."""

    def __init__(self, parent=None, accent: str = "#FF791C",
                 muted: str = "#8A8A8A"):
        super().__init__(parent)
        self._accent = accent
        self._asked = False           # đã hỏi cho việc hiện tại chưa
        self._answered = False

        lay = QHBoxLayout(self)
        # Sát lề, gọn chiều cao — để nó trông như một dòng chú thích, không
        # phải một khối giao diện mới.
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)

        self._lbl = QLabel(_QUESTION)
        self._lbl.setStyleSheet(
            "color:%s; font-size:12px; background:transparent;" % muted)
        lay.addWidget(self._lbl)

        self._up = self._mk_btn("👍", "Kết quả dùng được")
        self._down = self._mk_btn("👎", "Kết quả chưa dùng được")
        self._up.clicked.connect(lambda: self._answer("up"))
        self._down.clicked.connect(lambda: self._answer("down"))
        lay.addWidget(self._up)
        lay.addWidget(self._down)
        lay.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)

        self.setVisible(False)

    # ── nội bộ ───────────────────────────────────────────────────
    def _mk_btn(self, text: str, tip: str) -> QPushButton:
        """Nút phẳng, không viền — bấm được nhưng không tranh sự chú ý."""
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setFixedSize(30, 24)
        # objectName rỗng + stylesheet riêng: KHÔNG dính stylesheet chung của
        # app (nút chính màu cam to đùng), tránh biến câu hỏi phụ thành thứ nổi
        # nhất màn hình.
        b.setStyleSheet(
            "QPushButton{border:none; background:transparent; font-size:15px;"
            " padding:0px;}"
            "QPushButton:hover{background:rgba(0,0,0,0.07); border-radius:5px;}"
        )
        return b

    def _answer(self, value: str) -> None:
        if self._answered:
            return
        self._answered = True
        try:
            if tnt_track is not None:
                tnt_track.thumbs(value)
        except Exception:
            pass                                     # góp ý hỏng != app hỏng
        self._up.setVisible(False)
        self._down.setVisible(False)
        self._lbl.setText(_THANKS)
        self._lbl.setStyleSheet(
            "color:%s; font-size:12px; font-weight:600; background:transparent;"
            % self._accent)
        self._timer.start(int(THANKS_SEC * 1000))

    def _fade(self) -> None:
        self.setVisible(False)

    # ── API cho app ──────────────────────────────────────────────
    def reset(self) -> None:
        """Bắt đầu một việc mới: ẩn đi, sẵn sàng hỏi lại lúc có kết quả."""
        self._timer.stop()
        self._asked = False
        self._answered = False
        self._up.setVisible(True)
        self._down.setVisible(True)
        self._lbl.setText(_QUESTION)
        self._lbl.setStyleSheet(
            "color:#8A8A8A; font-size:12px; background:transparent;")
        self.setVisible(False)

    def ask(self) -> None:
        """Hiện câu hỏi. CHỈ gọi khi việc đã ra sản phẩm thật.

        Gọi lại nhiều lần trong cùng một việc cũng không hỏi lại — tránh trường
        hợp mỗi lần tải một file lại nhảy ra hỏi một lần.
        """
        if self._asked:
            return
        self._asked = True
        self.setVisible(True)
        self._timer.start(AUTO_HIDE_SEC * 1000)
