# Aki AOV Tools

Công cụ web và Telegram bot dùng chung bộ xử lý ZIP trên ổ đĩa, kèm chức năng kiểm kê cấu trúc gói tài nguyên AOV (chỉ đọc).

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: chọn tối đa 10 ZIP để kiểm tra cấu trúc hoặc ghép ZIP thông thường.
- Web API `/api/inspect` và Telegram `/inspect`: quét ZIP ngoài, `Resources.zip` lồng bên trong và các `.pkg.bytes` có cấu trúc ZIP.
- Nhận diện header AES AoV `22 4A 67 xx`, đọc header MSES v7 (version, số field, số record) theo tên tài nguyên.
- Chuẩn hóa đường dẫn `Resources/<version>/` để đối chiếu xung đột giữa gói mod lồng và ZIP tài nguyên rời.
- Telegram bot: gửi ZIP nhỏ trực tiếp hoặc link HTTPS tải ZIP trực tiếp; `/files` liệt kê file, `/merge` ghép ZIP thường, `/clear` xóa danh sách, `/status` xem giới hạn.
- Web `/api/merge` và Telegram `/merge` đều chặn ghép ZIP thường khi phát hiện cấu trúc AOV lồng hoặc bảng MSES, tránh tạo gói sai do ghi đè/không xử lý phụ thuộc.
- Bot xử lý ZIP theo luồng trên ổ đĩa tạm, tránh giữ toàn bộ file lớn trong RAM.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Chạy Telegram bot
Thiết lập `BOT_TOKEN` và `ALLOWED_USER_IDS` trong phần Environment/Secrets của nền tảng triển khai. `ALLOWED_USER_IDS` là danh sách Telegram ID dạng số, phân cách bằng dấu phẩy; toàn bộ bot sẽ từ chối tài khoản nếu biến này để trống. Không commit token hoặc ID vào GitHub.
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
Phần ghép hiện tại chỉ hợp nhất ZIP thông thường: giữ tệp trùng nội dung một lần và báo lỗi nếu cùng đường dẫn nhưng nội dung khác nhau. Bộ kiểm tra có thể giải mã header AES và nhận diện MSES v7, nhưng **chưa** có bộ parser/serializer record MSES, bộ ghép IFix, CommonActions XML nén Zstandard, hoặc bộ cập nhật đồng bộ ResourcePacker/ResourceVerification cho mọi gói AOV. Vì vậy các gói AOV có tài nguyên lồng/MSES sẽ bị chặn khỏi đường ghép ZIP thường thay vì tạo ra file có nguy cơ mất skin, hiệu ứng hoặc âm thanh. Chưa xem bất kỳ ZIP nào là bản mod đã xác nhận chạy trong game.
