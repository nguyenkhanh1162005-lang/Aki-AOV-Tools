# Aki AOV Tools

Công cụ web và Telegram bot để ghép các tệp ZIP thông thường.

## Tính năng hiện có
- Web UI tiếng Việt, dùng được trên điện thoại.
- Chọn tối đa 10 ZIP và tải về một ZIP kết quả.
- Telegram bot: gửi nhiều ZIP, dùng `/merge` để ghép; `/clear` để xóa danh sách.
- API kiểm tra sức khỏe tại `/health`.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Chạy Telegram bot
Thiết lập biến môi trường `BOT_TOKEN` bằng token bot trong phần Secrets của nền tảng triển khai. Không commit token vào GitHub.
```bash
python bot.py
```

## Giới hạn quan trọng
Phiên bản này chỉ ghép ZIP thông thường: giữ tệp trùng nội dung một lần và báo lỗi nếu cùng đường dẫn nhưng nội dung khác nhau. Nó **không** hợp nhất dữ liệu nhị phân độc quyền của Liên Quân như `BattleBank.bytes` hoặc `LobbyBank.bytes`. Chưa có thuật toán AOV skin merger được xác minh, vì vậy không xem ZIP kết quả là bản mod game đã hoàn thiện.

## Triển khai
Có thể triển khai web bằng Dockerfile. Telegram bot cần chạy như một worker riêng và cần biến môi trường `BOT_TOKEN`.
