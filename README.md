# Aki AOV Tools

Công cụ web và Telegram bot dùng chung bộ xử lý ZIP, kiểm kê cấu trúc và bộ ghép tài nguyên Liên Quân iOS theo phiên bản.

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: tải tối đa 10 ZIP theo khối 4 MB, kiểm tra cấu trúc, ghép ZIP thông thường hoặc ghép mod AOV chuyên sâu.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và đường dẫn trùng giữa các ZIP.
- Web API `/api/drive/import`: tải ZIP từ link Google Drive công khai trực tiếp về máy chủ, tối đa 10 link và tổng 500 MB.
- Web API `/api/upload/{upload_id}/aov-merge`: chạy bộ ghép Vấn Tâm Kiếm chuyên sâu trên phiên upload.
- Telegram: `/inspect`, `/merge`, `/aovmerge`, `/clear`, `/status`. Web và bot dùng chung engine AOV.

## Bộ ghép AOV được hỗ trợ

Bản hiện tại khóa theo đúng cặp tài nguyên đã kiểm tra:
- ZIP nền iOS 1.64.1.12: bộ 10 skin + nút bấm/thông báo.
- ZIP Vấn Tâm Kiếm Actor 595, dữ liệu 1.64.1.11: có thể là một ZIP hoặc nhiều phần ZIP.

Engine thực hiện:
- Ghép IFix của `Thanos/HeroSkin_2.bytes` vào `HeroSkin_1.bytes`, remap chỉ số type/method/field/string và kiểm tra lại patch.
- Ghép các bảng MSES `BattleBank.bytes`, `LobbyBank.bytes`, `skillcombine.bytes` và `ResSkinSeniorLabelCfg.bytes` theo khóa dòng; giữ schema nền mới hơn và chỉ thay/thêm các dòng Vấn Tâm Kiếm cần thiết. Riêng checksum MSES nền của `ResSkinSeniorLabelCfg` đang cũ, engine chỉ sửa lại sau khi xác minh đủ 53 dòng và cấu trúc bảng.
- Ghép track CommonActions dành cho skin ID 59500/59501.
- Cập nhật ResourcePackerInfoSet cho 10 AssetBundle Vấn Tâm Kiếm; cập nhật CrcXor trong ResourceVerificationInfoSet cho các file VTK thực sự được đưa vào kết quả và giữ giá trị 1.64.1.12 cho những file chỉ khác do lệch phiên bản.
- Thêm AssetBundle và soundbank iOS của Vấn Tâm Kiếm; giữ `KernelLua.pkg.bytes` và `StableSystems_3.pkg.bytes` của bản nền 1.64.1.12 để tránh hạ phiên bản hệ thống dùng chung.

Bộ ghép từ chối nếu phiên bản, số lượng AssetBundle, bảng chỉ mục hoặc dữ liệu dùng chung không khớp với cấu hình được hỗ trợ. Đây không phải bộ giải mã/hợp nhất tổng quát cho mọi bản mod Liên Quân.

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
- Khi bật Local Bot API: tối đa 500 MB mỗi ZIP, tổng đầu vào 500 MB, tối đa 10 ZIP; ZIP đầu ra tối đa 1 GB. Máy chủ vẫn cần đủ RAM và dung lượng ổ đĩa tạm.

## Nhập ZIP từ Google Drive
- Dán link ZIP nền và link Vấn Tâm Kiếm, mỗi link một dòng. Nếu VTK được chia thành nhiều phần, dán tất cả các link phần đó.
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
