"""TNT DOWNLOADER — tool tải video hàng loạt chạy TRÊN MÁY (PySide6).

Chạy:  python app.py     (hoặc bấm run.bat)

Hỗ trợ: TikTok (kể cả quảng cáo/Spark Ads), Douyin (kể cả quảng cáo), YouTube,
Facebook, Instagram, Shopee/1688/Taobao, link .mp4 trực tiếp...

Lợi thế so với bản web: dùng IP nhà + cookies trình duyệt của chính bạn nên tải
được cả video mà server không xem được. Cấu hình lưu tại ~/.tnt_downloader/config.json
"""

from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from core import cookies as ck
from core import sniffer
from core.engine import (Options, Result, detect_source, download_many,
                         parse_links, resolve_cookies)
from core.utils import (BROWSER_PROFILE_DIR, human_size, launch_persistent,
                        load_config, open_folder, save_config)
from tnt_license import check_license

# Tên tool trong license (phải khớp danh sách --tools lúc cấp license.key).
TOOL_NAME = "TNT_Downloader"

# --- Bộ màu thương hiệu TNT GROUP (đồng bộ với các tool khác) ---
MAROON = "#3B0000"
MAROON_SOFT = "#5A1414"
ORANGE = "#FF791C"
ORANGE_HOVER = "#FF8C3D"
CREAM = "#FBF6F2"

STYLESHEET = f"""
QWidget#central {{ background: {CREAM}; }}
QLabel {{ color: {MAROON}; }}
QFrame#header {{ background: #FFFFFF; border: 1px solid #ECD9D1; border-radius: 14px; }}
QGroupBox {{
    color: {MAROON}; font-weight: 600; border: 1px solid #ECD9D1;
    border-radius: 12px; margin-top: 10px; padding-top: 10px; background: #FFFFFF;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QTableWidget {{
    background: #FFFFFF; border: 1px solid #E3D2CB; border-radius: 8px;
    padding: 5px; color: #2A0000; selection-background-color: {ORANGE};
}}
QPushButton {{
    background: {ORANGE}; color: #FFFFFF; border: none; border-radius: 9px;
    padding: 8px 16px; font-weight: 600;
}}
QPushButton:hover {{ background: {ORANGE_HOVER}; }}
QPushButton:disabled {{ background: #D9C7C0; color: #FFFFFF; }}
QPushButton#ghost {{ background: #FFFFFF; color: {MAROON}; border: 1px solid #E3D2CB; }}
QPushButton#ghost:hover {{ background: #FFF1E8; }}
QHeaderView::section {{
    background: {MAROON}; color: #FFFFFF; padding: 6px; border: none; font-weight: 600;
}}
QProgressBar {{
    border: 1px solid #E3D2CB; border-radius: 8px; background: #FFFFFF;
    text-align: center; color: {MAROON}; height: 18px;
}}
QProgressBar::chunk {{ background: {ORANGE}; border-radius: 7px; }}
QCheckBox {{ color: {MAROON}; }}
"""

COLS = ["#", "Nguồn", "Link", "Trạng thái", "Tiến độ", "Dung lượng", "Tải bằng"]


class Worker(QThread):
    """Chạy cả mẻ tải ở luồng nền để GUI không đứng."""

    itemStart = Signal(int, str)
    itemProgress = Signal(int, int, int)
    itemDone = Signal(int, object)
    logLine = Signal(str)
    allDone = Signal(object)

    def __init__(self, urls: list[str], opts: Options):
        super().__init__()
        self.urls, self.opts = urls, opts
        self.cancel = threading.Event()

    def run(self):
        try:
            res = download_many(
                self.urls, self.opts,
                on_item_start=lambda i, u: self.itemStart.emit(i, u),
                on_item_progress=lambda i, g, t: self.itemProgress.emit(i, g, t),
                on_item_done=lambda i, r: self.itemDone.emit(i, r),
                on_log=lambda m: self.logLine.emit(m),
                cancel=self.cancel)
            self.allDone.emit(res)
        except Exception as e:                     # lỗi ngoài dự kiến -> báo, không sập app
            self.logLine.emit(f"LỖI: {e}")
            self.allDone.emit([])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TNT DOWNLOADER — Tải video hàng loạt (TikTok/Douyin/YouTube…)")
        self.resize(1180, 800)
        self.worker: Worker | None = None
        self.urls: list[str] = []
        self.results: list[Result] = []
        # mẻ đang chạy gồm những DÒNG nào trên bảng (chạy lại link lỗi -> chỉ vài dòng)
        self._rows: list[int] = []
        self._build_ui()
        self._load_cfg()

    # ────────────────────────────── giao diện ──────────────────────────────
    def _build_ui(self):
        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QFrame(objectName="header")
        hl = QHBoxLayout(header)
        title = QLabel("TNT DOWNLOADER")
        title.setFont(QFont("Segoe UI", 17, QFont.Bold))
        sub = QLabel("Tải video hàng loạt ngay trên máy — TikTok · Douyin · quảng cáo · YouTube · FB · Shopee")
        sub.setStyleSheet(f"color: {MAROON_SOFT};")
        box = QVBoxLayout()
        box.addWidget(title)
        box.addWidget(sub)
        hl.addLayout(box)
        hl.addStretch()
        root.addWidget(header)

        # ── ô nhập link ──
        gb_links = QGroupBox("1. Dán danh sách link (mỗi dòng 1 link)")
        vl = QVBoxLayout(gb_links)
        self.txt_links = QPlainTextEdit()
        self.txt_links.setPlaceholderText(
            "https://www.tiktok.com/@user/video/123...\n"
            "https://v.douyin.com/xxxxx/\n"
            "https://www.youtube.com/watch?v=...\n"
            "https://…/video_quang_cao.mp4")
        self.txt_links.setMinimumHeight(110)
        vl.addWidget(self.txt_links)
        row = QHBoxLayout()
        self.btn_parse = QPushButton("Phân tích link", objectName="ghost")
        self.btn_parse.clicked.connect(self.on_parse)
        self.btn_clear = QPushButton("Xoá danh sách", objectName="ghost")
        self.btn_clear.clicked.connect(self.on_clear)
        self.lbl_count = QLabel("0 link")
        row.addWidget(self.btn_parse)
        row.addWidget(self.btn_clear)
        row.addWidget(self.lbl_count)
        row.addStretch()
        vl.addLayout(row)
        root.addWidget(gb_links)

        # ── cấu hình ──
        gb_cfg = QGroupBox("2. Cấu hình")
        g = QGridLayout(gb_cfg)
        g.addWidget(QLabel("Thư mục lưu:"), 0, 0)
        self.ed_out = QLineEdit()
        btn_out = QPushButton("Chọn…", objectName="ghost")
        btn_out.clicked.connect(self.on_pick_out)
        btn_open = QPushButton("Mở thư mục", objectName="ghost")
        btn_open.clicked.connect(lambda: open_folder(self.ed_out.text().strip()))
        g.addWidget(self.ed_out, 0, 1, 1, 3)
        g.addWidget(btn_out, 0, 4)
        g.addWidget(btn_open, 0, 5)

        g.addWidget(QLabel("Tải song song:"), 1, 2)
        self.sp_conc = QSpinBox()
        self.sp_conc.setRange(1, 8)
        self.sp_conc.setValue(3)
        g.addWidget(self.sp_conc, 1, 3)

        g.addWidget(QLabel("Cookies từ:"), 1, 4)
        self.cb_browser = QComboBox()
        self.cb_browser.addItem("Không dùng", "")
        self.cb_browser.addItem("Trình duyệt TNT (khuyên dùng)", ck.TNT_PROFILE)
        for b in ck.available_browsers():
            self.cb_browser.addItem(b.capitalize(), b)
        self.cb_browser.setToolTip(
            "Phiên đăng nhập dùng để tải video quảng cáo / video riêng tư.\n\n"
            "• Trình duyệt TNT: bấm “Mở trình duyệt để đăng nhập” một lần — luôn chạy được.\n"
            "• Chrome/Edge trên Windows đời mới thường KHÔNG đọc được cookies\n"
            "  (App-Bound Encryption); Firefox thì vẫn đọc tốt.")
        g.addWidget(self.cb_browser, 1, 5)

        self.chk_browser = QCheckBox("Tự mở trình duyệt cho link khó (⚠ tải nhiều link sẽ bật cửa sổ liên tục)")
        self.chk_browser.setChecked(False)
        self.chk_browser.setToolTip(
            "TẮT (khuyên dùng): chỉ tải bằng API, chạy im lặng, không chiếm máy.\n"
            "BẬT: mỗi link mà mọi nguồn API đều thất bại sẽ mở một cửa sổ trình duyệt.\n\n"
            "Tải hàng trăm link thì để TẮT, xong mẻ hãy bấm “Thử lại link lỗi bằng "
            "trình duyệt” cho những dòng còn đỏ.")
        self.chk_headless = QCheckBox("Chạy ẩn cửa sổ trình duyệt")
        self.btn_login = QPushButton("Mở trình duyệt để đăng nhập", objectName="ghost")
        self.btn_login.clicked.connect(self.on_login_browser)
        g.addWidget(self.chk_browser, 2, 0, 1, 3)
        g.addWidget(self.chk_headless, 2, 3)
        g.addWidget(self.btn_login, 2, 4, 1, 2)

        g.addWidget(QLabel("File cookies.txt:"), 3, 0)
        self.ed_cookies = QLineEdit()
        self.ed_cookies.setPlaceholderText(
            "tuỳ chọn — file xuất từ extension “Get cookies.txt LOCALLY” (ưu tiên hơn ô Cookies từ)")
        btn_ck = QPushButton("Chọn…", objectName="ghost")
        btn_ck.clicked.connect(self.on_pick_cookies)
        g.addWidget(self.ed_cookies, 3, 1, 1, 4)
        g.addWidget(btn_ck, 3, 5)
        root.addWidget(gb_cfg)

        # ── chạy ──
        run_row = QHBoxLayout()
        self.btn_start = QPushButton("▶  BẮT ĐẦU TẢI")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop = QPushButton("■  Dừng", objectName="ghost")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_retry = QPushButton("Thử lại link lỗi bằng trình duyệt", objectName="ghost")
        self.btn_retry.setEnabled(False)
        self.btn_retry.setToolTip("Chạy lại CHỈ những dòng lỗi, có mở trình duyệt. "
                                  "Làm một lượt cuối mẻ thay vì bật cửa sổ suốt lúc tải.")
        self.btn_retry.clicked.connect(self.on_retry_failed)
        self.pb = QProgressBar()
        self.pb.setValue(0)
        run_row.addWidget(self.btn_start, 2)
        run_row.addWidget(self.btn_stop, 1)
        run_row.addWidget(self.btn_retry, 2)
        run_row.addWidget(self.pb, 4)
        root.addLayout(run_row)

        # ── bảng + log ──
        split = QSplitter(Qt.Vertical)
        self.tbl = QTableWidget(0, len(COLS))
        self.tbl.setHorizontalHeaderLabels(COLS)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        hh = self.tbl.horizontalHeader()
        for i, mode in enumerate([QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                  QHeaderView.Stretch, QHeaderView.ResizeToContents,
                                  QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                  QHeaderView.ResizeToContents]):
            hh.setSectionResizeMode(i, mode)
        self.tbl.doubleClicked.connect(self.on_row_open)
        self.tbl.setToolTip("Nhấp đúp 1 dòng đã tải xong để mở thư mục chứa file")
        split.addWidget(self.tbl)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        split.addWidget(self.log)
        split.setSizes([420, 200])
        root.addWidget(split, 1)

        self.setStyleSheet(STYLESHEET)

    # ────────────────────────────── cấu hình ──────────────────────────────
    def _load_cfg(self):
        c = load_config()
        default_out = os.path.join(os.path.expanduser("~"), "Downloads", "TNT_Videos")
        self.ed_out.setText(c.get("out_dir") or default_out)
        self.sp_conc.setValue(int(c.get("concurrency", 3)))
        b = c.get("browser", "")
        bi = self.cb_browser.findData(b)
        self.cb_browser.setCurrentIndex(bi if bi >= 0 else 0)
        self.chk_browser.setChecked(bool(c.get("browser_fallback_v2", False)))
        self.chk_headless.setChecked(bool(c.get("headless", False)))
        self.ed_cookies.setText(c.get("cookies_file", ""))

    def _save_cfg(self):
        save_config({
            "out_dir": self.ed_out.text().strip(),
            "concurrency": self.sp_conc.value(),
            "browser": self.cb_browser.currentData(),
            "browser_fallback_v2": self.chk_browser.isChecked(),
            "headless": self.chk_headless.isChecked(),
            "cookies_file": self.ed_cookies.text().strip(),
        })

    def _opts(self) -> Options:
        return Options(
            out_dir=self.ed_out.text().strip(),
            browser=self.cb_browser.currentData() or "",
            concurrency=self.sp_conc.value(),
            browser_fallback=self.chk_browser.isChecked(),
            headless=self.chk_headless.isChecked(),
            cookies_file=self.ed_cookies.text().strip(),
        )

    def on_pick_cookies(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn file cookies.txt",
                                           os.path.expanduser("~"), "Cookies (*.txt);;Tất cả (*)")
        if f:
            self.ed_cookies.setText(f)

    # ────────────────────────────── hành động ──────────────────────────────
    def log_line(self, msg: str):
        self.log.appendPlainText(msg)

    def on_pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu video",
                                             self.ed_out.text().strip() or os.path.expanduser("~"))
        if d:
            self.ed_out.setText(d)

    def on_clear(self):
        self.txt_links.clear()
        self.tbl.setRowCount(0)
        self.urls, self.results = [], []
        self.lbl_count.setText("0 link")
        self.pb.setValue(0)

    def on_parse(self):
        self.urls = parse_links(self.txt_links.toPlainText())
        self.results = [Result(url=u) for u in self.urls]
        self.tbl.setRowCount(0)
        for i, u in enumerate(self.urls):
            self.tbl.insertRow(i)
            self._set(i, 0, str(i + 1))
            self._set(i, 1, detect_source(u))
            self._set(i, 2, u)
            self._set(i, 3, "sẵn sàng")
            self._set(i, 4, "—")
            self._set(i, 5, "—")
            self._set(i, 6, "—")
        self.lbl_count.setText(f"{len(self.urls)} link")
        self.log_line(f"Đã nhận {len(self.urls)} link.")
        return self.urls

    def _set(self, row: int, col: int, text: str):
        it = QTableWidgetItem(text)
        if col == 2:
            it.setToolTip(text)
        self.tbl.setItem(row, col, it)

    def on_login_browser(self):
        """Mở Chromium hồ sơ riêng để đăng nhập TikTok/Douyin/Ads Manager một lần."""
        if not sniffer.available():
            QMessageBox.warning(self, "Thiếu Playwright",
                                "Cần cài Playwright để dùng chế độ trình duyệt:\n\n"
                                "pip install playwright\n"
                                "python -m playwright install chromium")
            return
        self.log_line("Mở trình duyệt — đăng nhập TikTok/Douyin rồi ĐÓNG cửa sổ lại. "
                      "Phiên đăng nhập được nhớ cho các lần tải sau.")
        threading.Thread(target=self._open_login, daemon=True).start()

    def _open_login(self):
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
            os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
            with sync_playwright() as p:
                ctx = launch_persistent(p, BROWSER_PROFILE_DIR, headless=False)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("https://www.tiktok.com/login")
                while ctx.pages:                   # chờ tới khi người dùng đóng cửa sổ
                    page.wait_for_timeout(1000)
        except Exception as e:
            self.log_line(f"Không mở được trình duyệt: {e}")

    def on_start(self):
        urls = self.urls or self.on_parse()
        if not urls:
            QMessageBox.information(self, "Chưa có link", "Hãy dán ít nhất 1 link video.")
            return
        out = self.ed_out.text().strip()
        if not out:
            QMessageBox.information(self, "Chưa chọn thư mục", "Hãy chọn thư mục lưu video.")
            return
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi thư mục", f"Không tạo được thư mục lưu:\n{e}")
            return

        self._save_cfg()
        opts = self._opts()
        # Kiểm tra cookies NGAY từ đầu: sai nguồn cookies là lý do số 1 khiến link
        # quảng cáo tải hỏng, báo trước còn hơn để chạy hết mẻ mới biết.
        if opts.browser or opts.cookies_file:
            cf = resolve_cookies(opts)
            self.log_line(f"Cookies: {os.path.basename(cf)}" if cf
                          else "⚠ " + (ck.last_error() or "không lấy được cookies từ nguồn đã chọn"))
        self.log_line(f"── Bắt đầu tải {len(urls)} link → {out}"
                      + ("" if opts.browser_fallback else "  (chỉ dùng API, không mở trình duyệt)"))

        self._start_batch(list(range(len(urls))), opts)

    def _start_batch(self, rows: list[int], opts: Options):
        """Chạy một mẻ tải cho các DÒNG `rows` trên bảng (cả bảng, hoặc chỉ dòng lỗi)."""
        self._rows = rows
        self.pb.setValue(0)
        self.done_count = 0
        self.btn_start.setEnabled(False)
        self.btn_retry.setEnabled(False)
        self.btn_stop.setEnabled(True)
        for i in rows:                       # xoá trạng thái cũ của các dòng sắp chạy lại
            self._set(i, 3, "chờ")
            self._set(i, 4, "—")

        self.worker = Worker([self.urls[i] for i in rows], opts)
        self.worker.itemStart.connect(self.on_item_start)
        self.worker.itemProgress.connect(self.on_item_progress)
        self.worker.itemDone.connect(self.on_item_done)
        self.worker.logLine.connect(self.log_line)
        self.worker.allDone.connect(self.on_all_done)
        self.worker.start()

    def on_stop(self):
        if self.worker:
            self.worker.cancel.set()
            self.log_line("Đã yêu cầu dừng — các link đang tải sẽ chạy nốt.")
            self.btn_stop.setEnabled(False)

    def _row(self, i: int) -> int:
        return self._rows[i] if i < len(self._rows) else i

    def on_item_start(self, i: int, url: str):
        self._set(self._row(i), 3, "đang tải")

    def on_item_progress(self, i: int, got: int, total: int):
        self._set(self._row(i), 4, f"{got * 100 // total}%" if total else human_size(got))

    def on_item_done(self, i: int, r: Result):
        i = self._row(i)
        self.results[i] = r
        if r.ok:
            self._set(i, 3, "✔ xong")
            self._set(i, 4, "100%")
            self._set(i, 5, human_size(r.size))
            self._set(i, 6, r.via)
            self.log_line(f"  ✔ [{i + 1}] {r.title} ({human_size(r.size)}) — {r.via}")
        else:
            self._set(i, 3, "✗ lỗi")
            self._set(i, 6, "—")
            it = self.tbl.item(i, 3)
            if it:
                it.setToolTip(r.error)
            self.log_line(f"  ✗ [{i + 1}] {r.error}")
        self.done_count += 1
        self.pb.setValue(int(self.done_count * 100 / max(1, len(self._rows))))

    def on_all_done(self, _res):
        ok = sum(1 for r in self.results if r.ok)
        failed = [i for i, r in enumerate(self.results) if not r.ok]
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.pb.setValue(100)
        self.log_line(f"── XONG: {ok} thành công · {len(failed)} lỗi")
        # Còn link lỗi -> mời dùng trình duyệt MỘT LƯỢT cho riêng chúng, thay vì
        # bật cửa sổ suốt lúc tải cả mẻ.
        can_retry = bool(failed) and sniffer.available() and not self.chk_browser.isChecked()
        self.btn_retry.setEnabled(can_retry)
        if can_retry:
            self.log_line(f"   {len(failed)} link lỗi — có thể bấm “Thử lại link lỗi bằng "
                          "trình duyệt” (sẽ mở cửa sổ, chỉ chạy cho các link này).")
        if ok:
            open_folder(self.ed_out.text().strip())

    def on_retry_failed(self):
        """Chạy lại CHỈ những dòng lỗi, lần này cho phép mở trình duyệt bắt luồng."""
        rows = [i for i, r in enumerate(self.results) if not r.ok]
        if not rows:
            return
        opts = self._opts()
        opts.browser_fallback = True
        opts.concurrency = 1          # 1 cửa sổ trình duyệt tại một thời điểm
        self.log_line(f"── Chạy lại {len(rows)} link lỗi bằng trình duyệt (mỗi lần 1 link)")
        self._start_batch(rows, opts)

    def on_row_open(self):
        row = self.tbl.currentRow()
        if 0 <= row < len(self.results) and self.results[row].ok:
            open_folder(os.path.dirname(self.results[row].path))

    def closeEvent(self, e):
        self._save_cfg()
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.worker.wait(3000)
        e.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TNT Downloader")
    # Kiểm license TRƯỚC khi mở cửa sổ chính. Sai/thiếu license -> tnt_license tự
    # hiện hộp thoại kèm MÃ MÁY (có nút Copy) rồi thoát.
    # QApplication phải tạo trước để hộp thoại đó dùng được giao diện Qt.
    check_license(TOOL_NAME)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
