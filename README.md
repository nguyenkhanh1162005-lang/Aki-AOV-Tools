# Aki AOV Tools

Công cụ web và Telegram bot dùng chung bộ xử lý ZIP trên ổ đĩa, kèm chức năng kiểm kê cấu trúc gói tài nguyên AOV (chỉ đọc).

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: chọn tối đa 10 ZIP để kiểm tra cấu trúc hoặc ghép ZIP thông thường.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và đường dẫn trùng giữa các ZIP.
- Telegram bot: dùng `/inspect` để tạo cùng loại báo cáo kiểm kê trước khi cân nhắc ghép.
- Telegram bot: gửi ZIP nhỏ trực tiếp hoặc gửi link HTTPS tải ZIP trực tiếp, dùng `/merge` để ghép, `/clear` để xóa danh sách, `/status` để xem giới hạn.
- Bot xử lý ZIP theo luồng trên ổ đĩa tạm, tránh giữ toàn bộ file lớn trong RAM.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Chạy Telegram bot
Thiết lập `BOT_TOKEN` và `ALLOWED_USER_IDS` trong phần Environment/Secrets của nền tảng triển khai. `ALLOWED_USER_IDS` là danh sách Telegram ID dạng số, phân cách bằng dấu phẩy; chức năng tải ZIP qua link sẽ bị khóa nếu biến này để trống. Không commit token hoặc ID vào GitHub.
```bash
python bot.py
```

### Giới hạn file Telegram
- Nếu gửi tệp đính kèm qua Bot API mặc định: bot chỉ tải được file tối đa 20 MB; code sẽ báo rõ giới hạn này.
- Phương án miễn phí tạm thời: gửi cho bot một **liên kết HTTPS tải trực tiếp tới file ZIP công khai**. Bot tải theo luồng xuống ổ đĩa tạm, kiểm tra ZIP và giới hạn tổng link là 500 MB. Link trang xem trước của Drive/Dropbox có thể không phải link tải trực tiếp và có thể bị từ chối.
- Với Bot API mặc định, bot chỉ gửi ZIP kết quả tối đa 50 MB. Nếu kết quả lớn hơn, bot giữ file nguồn và báo cần Local Bot API hoặc một kho lưu trữ kết quả riêng. Vì vậy link intake chưa thay thế hoàn toàn Local Bot API.
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

## Giới hạn về Liên Quân
Phiên bản này chỉ ghép ZIP thông thường: giữ tệp trùng nội dung một lần và báo lỗi nếu cùng đường dẫn nhưng nội dung khác nhau. Nó **không** hợp nhất dữ liệu nhị phân độc quyền của Liên Quân như `BattleBank.bytes` hoặc `LobbyBank.bytes`. Chưa có thuật toán AOV skin merger được xác minh, vì vậy không xem ZIP kết quả là bản mod game đã hoàn thiện.
