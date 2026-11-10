# Aki AOV Tools

Công cụ web và Telegram bot dùng chung bộ xử lý ZIP, kiểm kê cấu trúc và bộ ghép tài nguyên Liên Quân iOS theo phiên bản.

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: tải tối đa 10 ZIP theo khối 4 MB, kiểm tra cấu trúc, ghép ZIP thông thường hoặc ghép mod AOV chuyên sâu.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và so sánh SHA-256 các đường dẫn trùng để phân biệt nội dung giống hệt với xung đột thật (tối đa 80 đường dẫn trùng được hiển thị chi tiết).
- Web API `/api/capabilities`: công bố registry adapter, phân biệt định dạng chỉ kiểm tra với định dạng có bộ ghép đã xác minh; mặc định fail-closed.
- Bộ kiểm tra `/inspect` và `/api/inspect` đọc chữ ký 32 byte đầu mỗi mục để phân loại sơ bộ AoV AES/Zstandard, Unity AssetBundle, ZIP lồng, Wwise, ảnh, XML/JSON và dữ liệu chưa nhận diện; chỉ đọc, không sửa tài nguyên.
- Web API `/api/drive/import`: tải ZIP từ link Google Drive công khai trực tiếp về máy chủ, tối đa 10 link và tổng 500 MB.
- Web API `/api/upload/{upload_id}/aov-merge`: chạy bộ ghép Vấn Tâm Kiếm chuyên sâu trên phiên upload.
- Telegram: `/inspect`, `/merge`, `/aovmerge`, `/clear`, `/status`. Web và bot dùng chung engine AOV.

## Bộ ghép AOV được hỗ trợ

Bản hiện tại khóa theo đúng cặp tài nguyên đã kiểm tra:
- Gói MOD 10 skin iOS 1.64.1.12: bộ 10 skin + nút bấm/thông báo. Đây là gói mod do người dùng cung cấp, không phải gói game gốc.
- Gói MOD Vấn Tâm Kiếm Actor 595, dữ liệu 1.64.1.11: có thể là một ZIP hoặc nhiều phần ZIP.

**Quy tắc nguồn:** chỉ ghép gói mod với gói mod. Engine không tải, không yêu cầu và không chèn tài nguyên từ bộ cài Liên Quân gốc. Từ "nền" trong mô tả kỹ thuật cũ chỉ gói mod 10 skin được dùng làm pack đích, không có nghĩa là game gốc.

Engine thực hiện:
- Ghép IFix của `Thanos/HeroSkin_2.bytes` vào `HeroSkin_1.bytes`, remap chỉ số type/method/field/string và kiểm tra lại patch.
- Ghép các bảng MSES `BattleBank.bytes`, `LobbyBank.bytes`, `skillcombine.bytes` và `ResSkinSeniorLabelCfg.bytes` theo khóa dòng; giữ schema nền mới hơn và chỉ thay/thêm các dòng Vấn Tâm Kiếm cần thiết. Riêng checksum MSES nền của `ResSkinSeniorLabelCfg` đang cũ, engine chỉ sửa lại sau khi xác minh đủ 53 dòng và cấu trúc bảng.
- Ghép track CommonActions dành cho skin ID 59500/59501.
- Cập nhật ResourcePackerInfoSet cho 10 AssetBundle Vấn Tâm Kiếm; cập nhật CrcXor trong ResourceVerificationInfoSet cho các file VTK thực sự được đưa vào kết quả và giữ giá trị 1.64.1.12 cho những file chỉ khác do lệch phiên bản.
- Thêm AssetBundle và soundbank iOS của Vấn Tâm Kiếm; giữ `KernelLua.pkg.bytes` và `StableSystems_3.pkg.bytes` của bản nền 1.64.1.12 để tránh hạ phiên bản hệ thống dùng chung.

Bộ ghép từ chối nếu phiên bản, số lượng AssetBundle, bảng chỉ mục hoặc dữ liệu dùng chung không khớp với cấu hình được hỗ trợ. Bộ nhận diện định dạng chỉ giúp kiểm kê nhiều loại tài nguyên; nó không phải bộ giải mã/hợp nhất tổng quát cho mọi bản mod Liên Quân. API `/api/capabilities` công bố trạng thái chính xác của từng adapter: ZIP thường ghép theo quy tắc an toàn; cặp Vấn Tâm Kiếm được hỗ trợ theo phiên bản khóa; AoV AES/Zstandard, MSES/IFix ngoài cặp khóa, Unity AssetBundle, Wwise BNK và tài nguyên văn bản/ảnh/âm thanh khác hiện chỉ được kiểm tra hoặc nhận diện. Engine từ chối ghi đầu ra khi chưa có adapter tương thích, không âm thầm ghi đè.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Chạy Telegram bot
Thiết lập biến môi trường `BOT_TOKEN` trong phần Secrets của nền tảng triển khai. Không commit token vào GitHub.
```bash
python bot.py
```

### Giới hạn file Telegram
- Bot API mặc định chỉ tải được file tối đa 20 MB.
- Để nhận ZIP VTK khoảng 141 MB và gửi ZIP kết quả lớn, cần Local Bot API Server với cờ `--local`.
- Khi đã có Local Bot API Server, thêm hai biến môi trường vào dịch vụ bot:
  - `TELEGRAM_API_BASE_URL=http://<host-noi-bo>:8081/bot`
  - `TELEGRAM_FILE_BASE_URL=http://<host-noi-bo>:8081/file/bot`
- Hai URL phải trỏ tới Local Bot API Server có thể truy cập từ bot. Không dùng URL ví dụ nếu chưa triển khai server.
- Local Bot API Server cần `TELEGRAM_API_ID` và `TELEGRAM_API_HASH` lấy từ https://my.telegram.org. Không đưa các giá trị này lên GitHub.
- Trước khi chuyển bot từ Bot API chính thức sang Local Bot API Server, gọi `logOut` trên Bot API chính thức, dừng worker cũ, rồi mới đổi URL và khởi động worker mới.
- Khi bật Local Bot API theo cấu hình hiện tại: tối đa 500 MB mỗi ZIP, tổng đầu vào 500 MB, tối đa 10 ZIP và giới hạn gửi ZIP kết quả 500 MB. Máy chủ vẫn cần đủ RAM và dung lượng ổ đĩa tạm.

## Triển khai web + Telegram với Local Bot API

Để bot nhận ZIP lớn (như các phần VTK hơn 20 MB) và gửi ZIP kết quả lớn, có thể dùng Docker Compose trên VPS có Docker. Cấu hình mẫu chạy web, bot worker và Local Bot API trong cùng mạng riêng:

```sh
cp .env.example .env
# Điền BOT_TOKEN, TELEGRAM_API_ID và TELEGRAM_API_HASH trong .env
docker compose -f docker-compose.local-bot-api.yml up -d --build
```

Đọc [hướng dẫn triển khai Local Bot API](docs/telegram-local-api.md) trước khi chuyển bot từ máy chủ Telegram mặc định. Phải dừng worker cũ và gọi `logOut` đúng quy trình; không chạy hai polling worker cùng một bot. Không đưa file `.env` lên GitHub.

## Nhập ZIP từ Google Drive
- Dán link gói MOD 10 skin và link các phần gói MOD Vấn Tâm Kiếm, mỗi link một dòng. Không cần link bộ cài game gốc.
- File phải bật quyền “Bất kỳ ai có đường liên kết”.
- Chỉ nhận link `drive.google.com` hoặc `docs.google.com`; máy chủ tự tải file, giới hạn 500 MB mỗi ZIP và tổng 500 MB.
- Sau khi tải và kiểm tra, dùng nút ghép AOV chuyên sâu trước khi phiên upload hết hạn (1 giờ).

## Triển khai
- Web: triển khai riêng bằng Dockerfile hiện có.
- Telegram bot: triển khai như worker riêng, lệnh chạy `python bot.py`, có biến `BOT_TOKEN`.
- Local Bot API Server: dịch vụ riêng, cần Docker và ổ đĩa đủ lớn. Worker bot phải truy cập được server qua mạng nội bộ hoặc URL riêng an toàn.

## Giới hạn và xác minh
- CI chạy unit tests và kiểm tra cú pháp Python.
- Bộ ghép kiểm tra CRC ZIP, checksum MSES, IFix index, XML CommonActions và cấu trúc PackerInfo.
- Chưa có xác nhận chạy thực tế trong Liên Quân iOS. ZIP kết quả phải được thử trong game; không xem kiểm tra cấu trúc là bảo đảm tương thích 100%.
- Chỉ hỗ trợ đúng cặp phiên bản nêu trên. Không tự áp dụng cho bản game/skin khác.

### Lập kế hoạch tương thích tài nguyên (chỉ tư vấn)

API `POST /api/compatibility/plan` nhận manifest JSON dạng `{"resources":[...]}`. Mỗi mục cần `path`, `sha256` (64 ký tự hex) và `format`; có thể thêm `source`, `game_version`, `platform`, `actor_id`, `skin_id`, `is_aov_resource` và `dependencies`.

API phân loại từng đường dẫn thành `SAFE_MERGE`, `IDENTICAL_DUPLICATE`, `NEEDS_RULE`, `UNSUPPORTED` hoặc `VERSION_CONFLICT`. Đây là báo cáo tư vấn từ metadata được gửi lên, không phải bộ phân tích ZIP tự động, không xác thực payload và tuyệt đối không cấp quyền cho bộ ghép bỏ qua adapter. Chỉ executor với adapter đã xác minh mới được tạo ZIP đầu ra.

API `POST /api/compatibility/inspect` nhận nhiều ZIP qua multipart field `files`, tự lập manifest chỉ đọc (đường dẫn, định dạng chữ ký, SHA-256, kích thước, CRC32, nguồn) rồi chạy compatibility planner. Giới hạn hiện tại: tối đa 10 ZIP, 500 MB tổng đầu vào, 20.000 resource và 1 GiB tổng dữ liệu giải nén. API không giải mã AoV AES, không suy diễn dependency Unity/Wwise từ tên file và không sửa payload. Các resource AoV được đánh dấu cần adapter riêng; kết quả không phải quyền ghép.

Với mục AssetBundle UnityFS có kích thước không quá 64 MiB, manifest còn thử đọc metadata AssetBundle để lấy danh sách `m_Dependencies` và external CAB paths ở chế độ chỉ đọc. Kết quả có cờ `dependency_inventory_complete`; nếu parser lỗi, dữ liệu vượt giới hạn hoặc quét tham chiếu chưa đầy đủ thì dependency được đánh dấu chưa hoàn chỉnh. Đây chưa phải xác minh mọi dependency đã được đóng gói, và không tạo ra quyền sửa hoặc ghép bundle.

Compatibility planner giữ riêng các trường `unity_dependencies`, `unity_external_paths` và `dependency_inventory_complete` trong báo cáo cho từng AssetBundle. Các tham chiếu Unity này không bị ép thành đường dẫn ZIP thông thường để tránh kết luận sai về dependency còn thiếu.

Trong báo cáo tương thích, `SAFE_MERGE` chỉ dành cho tệp văn bản tài liệu không thuộc tài nguyên AoV (ví dụ `.txt`, `.md`, `.rst`, `.license`) và chỉ có nghĩa là có thể thêm đường dẫn ở cấp ZIP. Ảnh, âm thanh, JSON cấu hình, bundle và các tệp có thể được game sử dụng vẫn cần quy tắc riêng; không được hiểu `SAFE_MERGE` là bảo đảm chạy trong Liên Quân.
