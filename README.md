# TNT DOWNLOADER — tool tải video chạy trên máy

Bản **local** của chức năng "Down list video" trên web `website_content`, viết lại
cho nhanh và mạnh hơn khi chạy tại máy người dùng.

Hỗ trợ: **TikTok (kể cả quảng cáo / Spark Ads)**, **Douyin (kể cả quảng cáo)**,
YouTube, Facebook, Instagram, Shopee/1688/Taobao, và link `.mp4/.m3u8` trực tiếp.

---

## 1. Cài & chạy

```bash
run.bat
```

`run.bat` tự tạo `.venv`, cài thư viện rồi mở app. Chạy tay:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Yêu cầu: Python 3.10+. Không cần cài ffmpeg riêng (đã có `imageio-ffmpeg`), nhưng
cài ffmpeg hệ thống thì nhanh hơn và có `ffprobe` để lấy thời lượng video.

Chế độ "bắt luồng bằng trình duyệt" dùng **Microsoft Edge / Chrome cài sẵn** trên
máy — KHÔNG cần `playwright install chromium` (chỉ chạy lệnh đó nếu máy không có
cả Edge lẫn Chrome). Ép một trình duyệt cụ thể bằng biến `TNT_BROWSER_CHANNEL=msedge|chrome|chromium`.

## 2. Dùng

1. Dán danh sách link (mỗi dòng 1 link) → **Phân tích link**.
2. Chọn thư mục lưu và số link tải song song. Video luôn tải ở **chất lượng gốc** như trên link.
3. Muốn tải **video quảng cáo / video riêng tư** thì phải có phiên đăng nhập — xem mục 3.
4. **BẮT ĐẦU TẢI**. Nhấp đúp một dòng đã xong để mở thư mục chứa file.

Chạy dòng lệnh (tự động hoá):

```bash
python cli.py -o D:\videos --browser tnt --jobs 5 links.txt
```

## 3. Cookies — phần quyết định việc tải được video quảng cáo

Chọn ở ô **Cookies từ**:

| Nguồn | Khi nào dùng |
|---|---|
| **Trình duyệt TNT** *(khuyên dùng)* | Bấm **Mở trình duyệt để đăng nhập** → đăng nhập TikTok/Douyin/Ads Manager trong cửa sổ hiện ra → đóng cửa sổ. App nhớ phiên mãi. Luôn chạy được. |
| Firefox | Đang đăng nhập sẵn trên Firefox — đọc cookies tốt. |
| Chrome / Edge | ⚠ Trên Windows đời mới **thường thất bại**: từ Chrome 127 cookies được mã hoá App-Bound Encryption, tiến trình ngoài không giải mã nổi ([yt-dlp #10927](https://github.com/yt-dlp/yt-dlp/issues/10927)). Cũng hỏng nếu trình duyệt đang mở. |
| **File cookies.txt** | Chắc ăn nhất khi máy khoá chặt: xuất bằng extension *Get cookies.txt LOCALLY* rồi trỏ vào ô "File cookies.txt". Ưu tiên hơn mọi nguồn khác. |

App kiểm tra cookies ngay khi bấm tải và báo lý do cụ thể nếu không lấy được —
không để chạy hết cả mẻ mới biết hỏng.

## 4. Vì sao bản local tải được video quảng cáo còn bản web thì không

| | Web (Hugging Face Space) | Tool local |
|---|---|---|
| IP | datacenter → tikwm/YouTube hay chặn 403 | IP nhà, sạch |
| Cookies | không có phiên đăng nhập của bạn | dùng phiên đăng nhập ngay trên máy bạn |
| Quảng cáo chỉ hiện với tài khoản được target | không cách nào thấy | thấy, vì chạy bằng chính phiên của bạn |
| Trang preview Ads Manager (render bằng JS) | không mở nổi | mở bằng Chromium thật rồi bắt luồng video |
| Tốc độ | tuần tự từng link, phải qua relay | song song tới 8 link, tải thẳng |
| Bảo mật | đăng nhập tài khoản web | license.key ký Ed25519, khoá theo mã máy |

## 5. Thứ tự nguồn (engine tự thử, ra video thật là dừng)

| Loại link | Thứ tự thử |
|---|---|
| `.mp4` / `.m3u8` thẳng | tải luôn |
| Douyin | iesdouyin (không cần cookies) → yt-dlp → quét trang → trình duyệt |
| TikTok | yt-dlp (cookies + giả lập TLS) → tikwm → ssstik → trình duyệt |
| Trang quảng cáo (ads.tiktok, oceanengine…) | quét trang → yt-dlp generic → trình duyệt |
| Còn lại | yt-dlp → yt-dlp `best` → generic → quét trang → trình duyệt |

"Trình duyệt" = mở Edge/Chrome sẵn có với hồ sơ riêng (`~/.tnt_downloader/browser_profile`),
nghe request mạng của trang rồi tải lấy luồng video. Bấm **Mở trình duyệt để
đăng nhập** một lần, các lần sau tự nhớ phiên.

## 6. Xử lý khi tải lỗi

| Lỗi hay gặp | Cách xử lý |
|---|---|
| TikTok/Douyin quảng cáo báo lỗi hết nguồn | dùng **Trình duyệt TNT** (mục 3) + bật **bắt luồng bằng trình duyệt** |
| "Impersonate target chrome is not available" | `pip install -U curl_cffi` (cần >= 0.11) |
| Không đọc được cookies Chrome/Edge | đóng hẳn trình duyệt rồi thử lại; vẫn hỏng thì dùng **Trình duyệt TNT** hoặc file cookies.txt (mục 3) |
| YouTube báo "Sign in to confirm you're not a bot" | chọn nguồn cookies có đăng nhập YouTube (mục 3) |
| Trình duyệt không thấy luồng video | tắt "chạy ẩn", tự bấm play/đăng nhập trong cửa sổ hiện ra rồi chạy lại |
| Link cũ tự nhiên hỏng | `pip install -U yt-dlp` (site đổi liên tục) |

## 7. Cấu trúc

```
tnt_downloader/
  app.py               GUI PySide6
  cli.py               bản dòng lệnh
  core/
    engine.py          điều phối: chọn nguồn, tải song song
    extractors.py      tikwm · ssstik · iesdouyin · quét trang · link CDN thẳng
    sniffer.py         mở Edge/Chrome thật, bắt luồng video (cho link quảng cáo)
    cookies.py         nguồn cookies: hồ sơ TNT · trình duyệt máy · file cookies.txt
    utils.py           ffmpeg/ffprobe, mở trình duyệt, kiểm tra file video, cấu hình
  tnt_license.py       lớp bảo mật dùng chung (copy từ tnt_license_kit)
  tnt_downloader.spec  build .exe bằng PyInstaller (nhúng ffmpeg, mượn Edge/Chrome)
```

Cấu hình lưu ở `~/.tnt_downloader/config.json`; hồ sơ trình duyệt ở
`~/.tnt_downloader/browser_profile` — không nằm trong thư mục tool, nên mỗi người
dùng có phiên đăng nhập riêng trên máy họ.

## 8. Bảo mật license

Tool dùng chung lớp bảo mật ở `tnt_license_kit`: chữ ký số **Ed25519** + khoá theo
**mã máy**, tên tool trong license là **`TNT_Downloader`**.

- Không có `license.key` hợp lệ → app hiện **mã máy** (kèm nút Copy) rồi thoát;
  bản CLI in mã máy ra console. Không chạy được gì thêm.
- Nơi tool tìm license, theo thứ tự: biến môi trường `TNT_LICENSE_PATH` →
  **cạnh file exe/script** → `C:\TNT\license.key`.
- Một `license.key` dùng chung cho mọi tool TNT trên đúng máy đó.

**Cấp license cho một máy:**

```bash
cd ..\tnt_license_kit
python make_license.py --machine <MÃ_MÁY> --tools TNT_Downloader --note "Ten nhan vien" --out D:\giao\license.key
```

Bỏ `--tools` nếu muốn license đó dùng được mọi tool. Thêm `--expires 2026-12-31`
nếu muốn giới hạn thời gian.

## 9. Build .exe

```bash
pip install -r requirements.txt pyinstaller
pyinstaller tnt_downloader.spec
```

Ra `dist/TNT_Downloader/TNT_Downloader.exe` (~361 MB), đã **nhúng sẵn ffmpeg**;
trình duyệt thì mượn Edge/Chrome có sẵn trên máy đích. Phát hành: copy nguyên thư
mục `dist/TNT_Downloader` rồi **đặt `license.key` của máy đó cạnh file .exe** là xong.

### Dung lượng

Bản build đầu nặng **1.3 GB**, đã cắt còn **361 MB**:

| Cắt gì | Tiết kiệm | Cách |
|---|---|---|
| Chromium nhúng | −685 MB | dùng Edge/Chrome cài sẵn (`channel=`) |
| `ffprobe.exe` | −97 MB | thiếu ffprobe thì kiểm tra video bằng chính ffmpeg |
| `imageio_ffmpeg` | −84 MB | trùng với `ffmpeg.exe` đã nhúng |
| numpy / PIL / tkinter / Qt thừa | −35 MB | `EXCLUDES` trong spec |

Còn lại: playwright (node driver) 105 · ffmpeg 97 · PySide6 93 · Python + yt-dlp ~66.

### Bản macOS (.app)

PyInstaller **không cross-compile** — phải build trên máy Mac. Không có Mac thì
dùng GitHub Actions (miễn phí, có sẵn runner macOS):

> Repo → tab **Actions** → **Build macOS app** → **Run workflow** → tải artifact
> `TNT_Downloader-mac-arm64` (Apple Silicon) hoặc `-intel`.
> Đẩy tag `v*` cũng tự build cả hai.

Có máy Mac thì chạy thẳng:

```bash
bash build_mac.sh                     # bản gọn, dùng Chrome/Edge có sẵn
BUNDLE_CHROMIUM=1 bash build_mac.sh   # nhúng luôn Chromium (+~300MB)
```

Ra `dist/TNT_Downloader.app` + `dist/TNT_Downloader-mac.zip`. Vẫn quy tắc cũ:
**đặt `license.key` cạnh file .app**.

App được ad-hoc codesign (arm64 bắt buộc có chữ ký) nhưng **chưa notarize**, nên
lần đầu máy Mac sẽ chặn. Người dùng làm 1 lần:

```bash
xattr -dr com.apple.quarantine TNT_Downloader.app
```

hoặc chuột phải → **Open** → Open. Muốn double-click mượt hẳn cần tài khoản Apple
Developer ($99/năm) để notarize.

Công tắc trong `tnt_downloader.spec` nếu muốn nhẹ hơn nữa:
`BUNDLE_PLAYWRIGHT=False` (−105 MB, mất đường tải quảng cáo bằng trình duyệt),
`BUNDLE_FFMPEG=False` (−97 MB, máy đích phải tự có ffmpeg trong PATH),
`BUNDLE_CHROMIUM=True` chỉ bật khi máy đích không có cả Edge lẫn Chrome (+413 MB).
Muốn nhỏ nữa thì đặt một bản ffmpeg gọn vào `vendor/ffmpeg/` — spec ưu tiên lấy ở đó.
