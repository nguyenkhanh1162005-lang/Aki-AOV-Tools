# Aki AOV Tools

Công cụ web và Telegram bot dùng chung bộ xử lý ZIP trên ổ đĩa, kèm chức năng kiểm kê cấu trúc gói tài nguyên AOV (chỉ đọc).

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: chọn tối đa 10 ZIP để kiểm tra cấu trúc hoặc ghép ZIP thông thường.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và đường dẫn trùng giữa các ZIP.\n- Web API `/api/drive/import`: tải ZIP từ link chia sẻ Google Drive công khai trực tiếp về máy chủ, hỗ trợ tối đa 10 link và tổng 500 MB. Sau khi nhập, dùng phiên upload trả về để kiểm tra cấu trúc.
- Telegram bot: dùng `/inspect` để tạo cùng loại báo cáo kiểm kê trước khi cân nhắc ghép.
- Telegram bot: gửi ZIP, dùng `/merge` để ghép, `/clear` để xóa danh sách, `/status` để xem giới hạn.
- Bot xử lý ZIP theo luồng trên ổ đĩa tạm, tránh giữ toàn bộ file lớn trong RAM.

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
- Nếu dùng Bot API mặc định: bot chỉ tải được file tối đa 20 MB; code sẽ báo rõ giới hạn này.
- Để nhận ZIP tới 500 MB và gửi ZIP kết quả lớn, cần tự chạy **Local Bot API Server** với cờ `--local`.
- Khi đã có Local Bot API Server, thêm hai biến môi trường vào dịch vụ bot:
  - `TELEGRAM_API_BASE_URL=http://<host-noi-bo>:8081/bot`
  - `TELEGRAM_FILE_BASE_URL=http://<host-noi-bo>:8081/file/bot`
- Cả hai URL phải trỏ tới Local Bot API Server có thể truy cập được từ bot. Không dùng URL ví dụ ở trên nếu chưa triển khai server.
- Local Bot API Server cần `TELEGRAM_API_ID` và `TELEGRAM_API_HASH` lấy từ https://my.telegram.org. Không đưa các giá trị này lên GitHub.
- Trước khi chuyển bot từ máy chủ Telegram chính thức sang Local Bot API Server, phải gọi phương thức `logOut` trên Bot API chính thức, dừng worker cũ, rồi mới đổi URL và khởi động worker mới. Làm đúng thứ tự để tránh hai máy chủ cùng nhận updates.
- Mức giới hạn của code hiện tại khi bật Local Bot API: tối đa 500 MB mỗi ZIP, tổng file đầu vào 500 MB, tối đa 10 ZIP; ZIP đầu ra tối đa 1 GB. Máy chủ triển khai vẫn cần đủ RAM và dung lượng ổ đĩa tạm.

## Triển khai
- Web: triển khai riêng bằng Dockerfile hiện có.
- Telegram bot: triển khai như worker riêng, lệnh chạy `python bot.py`, có biến `BOT_TOKEN`.
- Local Bot API Server: là dịch vụ riêng, cần Docker và ổ đĩa lưu trữ đủ lớn. Worker bot phải truy cập được server qua mạng nội bộ hoặc URL riêng an toàn.

### Nhập ZIP từ Google Drive\n- Dán mỗi link chia sẻ Google Drive trên một dòng trong giao diện web. File phải bật quyền “Bất kỳ ai có đường liên kết”.\n- Chỉ nhận link `drive.google.com` hoặc `docs.google.com`; máy chủ tự tải nội dung, giới hạn 500 MB mỗi file và tổng 500 MB.\n- Phiên tải tạm hết hạn sau 1 giờ và file sẽ bị xóa sau khi kiểm tra xong hoặc khi phiên hết hạn.\n- Đây là nhập file để kiểm tra, không phải chức năng giải mã hoặc hợp nhất tài nguyên game.\n\n## Giới hạn về Liên Quân
Phiên bản này chỉ ghép ZIP thông thường: giữ tệp trùng nội dung một lần và báo lỗi nếu cùng đường dẫn nhưng nội dung khác nhau. Nó **không** hợp nhất dữ liệu nhị phân độc quyền của Liên Quân như `BattleBank.bytes` hoặc `LobbyBank.bytes`. Chưa có thuật toán AOV skin merger được xác minh, vì vậy không xem ZIP kết quả là bản mod game đã hoàn thiện.
