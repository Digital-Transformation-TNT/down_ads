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
   Mặc định app **chỉ chạy bằng API, không mở trình duyệt** — tải hàng trăm link vẫn
   im lặng, máy dùng việc khác bình thường.
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

**Douyin không cần cookies**: đi qua `savetik`, không cần phiên đăng nhập.
Cookies chỉ cần cho TikTok quảng cáo và video riêng tư.

> Douyin bị giới hạn tần suất: đo được savetik.co chỉ chịu ~**1 lượt hỏi / 2 giây**,
> gọi dồn là 429 hàng loạt. Tool tự giữ nhịp 2,5s cho *toàn bộ* các luồng tải nên
> không dính, đổi lại mỗi link Douyin tốn ~3 giây phần bóc link (300 link ≈ 16 phút).
> Phần tải file vẫn chạy song song bình thường.

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

Tất cả đều là gọi HTTP thuần (kèm cookies nếu có) — **không mở trình duyệt**:

| Loại link | Thứ tự thử |
|---|---|
| `.mp4` / `.m3u8` thẳng | tải luôn |
| Douyin | **savetik** — chỉ một hướng, thử lại 3 lượt (không cần cookies) |
| TikTok | yt-dlp (cookies + giả lập TLS) → API tiktok (cookies) → tikwm → **savetik** → ssstik |
| Trang quảng cáo (ads.tiktok, oceanengine…) | quét trang → API tiktok → yt-dlp generic |
| Còn lại | yt-dlp → yt-dlp `best` → generic → quét trang |

### Khi nào mới mở trình duyệt

Chỉ 2 trường hợp, đều do người dùng chủ động bấm:

1. Nút **Mở trình duyệt để đăng nhập** — đăng nhập TikTok/Douyin/Ads Manager một lần,
   app nhớ phiên ở `~/.tnt_downloader/browser_profile`. Sau đó việc *đọc* cookies từ hồ
   sơ này **không** mở trình duyệt nữa (đọc thẳng file SQLite).
2. Nút **Thử lại link lỗi bằng trình duyệt** — hiện ra sau khi chạy xong mẻ, chạy lại
   *chỉ* những dòng lỗi, mỗi lần 1 link, có mở cửa sổ để bắt luồng.

Ô **"Tự mở trình duyệt cho link khó"** mặc định TẮT. Bật lên thì mỗi link mà mọi
nguồn API đều thất bại sẽ mở một cửa sổ — tải hàng trăm link đừng bật.

### Tải chạy nền, không chiếm máy

Lúc tải app **không đụng gì tới Chrome/Edge đang mở** của bạn: không mở, không đóng,
không đọc phiên đang chạy. Việc còn lại có thể ảnh hưởng máy:

| Thứ | Ảnh hưởng | Cách chỉnh |
|---|---|---|
| Băng thông | tải song song 7–8 link ăn gần hết đường truyền, lướt web/họp online chậm theo | để **Tải song song = 3–4** |
| CPU | chỉ nặng khi gặp bài **ảnh/slideshow** (phải ghép ảnh + nhạc bằng ffmpeg). Video thường chỉ là tải file | — |
| Cửa sổ bật lên | mặc định KHÔNG bật gì: không mở trình duyệt, cũng không tự mở thư mục khi xong | bật lại bằng ô **"Mở thư mục khi tải xong"** |
| macOS lần đầu | có thể hiện hộp Keychain xin quyền đọc cookies | bấm **Always Allow**, các lần sau im lặng |

## 6. Xử lý khi tải lỗi

| Lỗi hay gặp | Cách xử lý |
|---|---|
| TikTok/Douyin quảng cáo báo lỗi hết nguồn | chọn Cookies = **Trình duyệt TNT** (mục 3); còn lỗi thì bấm **Thử lại link lỗi bằng trình duyệt** |
| Chrome cứ bật lên liên tục khi tải nhiều link | tắt ô "Tự mở trình duyệt cho link khó" (từ bản 1.1.0 đã mặc định tắt) |
| "Impersonate target chrome is not available" | `pip install -U curl_cffi` (cần >= 0.11) |
| Không đọc được cookies Chrome/Edge | đóng hẳn trình duyệt rồi thử lại; vẫn hỏng thì dùng **Trình duyệt TNT** hoặc file cookies.txt (mục 3) |
| YouTube báo "Sign in to confirm you're not a bot" | chọn nguồn cookies có đăng nhập YouTube (mục 3) |
| Trình duyệt không thấy luồng video | tắt "chạy ẩn", tự bấm play/đăng nhập trong cửa sổ hiện ra rồi chạy lại |
| Link cũ tự nhiên hỏng | `pip install -U yt-dlp` (site đổi liên tục) |
| Douyin báo "không có link video" | trang share iesdouyin đã bị Douyin rút ruột (2026) — bản 1.2.0 trở lên đi đường savetik, cập nhật tool là chạy |
| macOS: để license.key cạnh .app mà vẫn báo chưa có key | App Translocation — kéo app vào Applications, hoặc `xattr -dr com.apple.quarantine TNT_Downloader.app`, hoặc đặt key vào `~/Library/Application Support/TNT/` (xem mục 8) |

## 7. Cấu trúc

```
tnt_downloader/
  app.py               GUI PySide6
  cli.py               bản dòng lệnh
  core/
    engine.py          điều phối: chọn nguồn, tải song song
    extractors.py      savetik · tikwm · ssstik · API tiktok/douyin · quét trang · link CDN
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
  **cạnh file exe / .app** → `C:\TNT\license.key` (Windows) hoặc
  `/Library/Application Support/TNT/` (macOS) → rồi các chỗ dự phòng:
  `~/Library/Application Support/TNT/`, `~/Downloads`, `~/Desktop`, `/Applications`.
- Chấp nhận cả tên `license.key.txt` / `license.txt` — Finder và Explorer mặc định
  ẩn đuôi file nên người dùng rất hay lưu nhầm thành `.txt`.

> ⚠ **macOS — App Translocation.** App tải từ mạng mang cờ `com.apple.quarantine`.
> Mở lần đầu bằng double-click, macOS **không chạy app tại chỗ** mà copy vào một
> thư mục ngẫu nhiên chỉ-đọc `/private/var/folders/…/AppTranslocation/…` rồi chạy
> bản đó — nên "cạnh app" lúc chạy là thư mục ngẫu nhiên kia, và `license.key` đặt
> đúng chỗ vẫn **không được nhìn thấy**. Từ v1.1.3 app tự dò ra chỗ đặt app thật
> nên vẫn chạy được; muốn dứt điểm thì kéo app vào **Applications**, hoặc chạy
> `xattr -dr com.apple.quarantine TNT_Downloader.app`.
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
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python -m PyInstaller tnt_downloader.spec
```

⚠ **Luôn build trong venv sạch.** Build bằng Python hệ thống/Anaconda dùng chung
thì gói nào có đăng ký hook PyInstaller cũng tự chui vào bản phát hành — đã dính
thật với `patchright` (fork của Playwright do tool khác cài), nó bơm thêm 102 MB
dù tool không hề import, và `excludes` không chặn được.

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
