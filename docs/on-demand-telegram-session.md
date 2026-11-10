# Chạy Aki-AOV Telegram theo phiên, không chạy 24/7

## Mục tiêu

Chỉ bật dịch vụ khi chuẩn bị gửi mod, giữ dịch vụ chạy trong lúc Telegram nhận ZIP, ghép và gửi ZIP kết quả, rồi chủ động tắt. Không cần để bot chạy suốt ngày.

**Giới hạn quan trọng:** khi dịch vụ đã tắt hoàn toàn, bot không thể nhận tin nhắn mới và không thể tự bật lại. Người dùng phải khởi động phiên trên máy chủ trước khi gửi file.

## Điều kiện

- Máy chủ Linux có Docker Engine và Docker Compose.
- Có đủ RAM, dung lượng ổ đĩa và băng thông để giữ ZIP nguồn, dữ liệu giải nén tạm và ZIP đầu ra cùng lúc. Với đầu vào 500 MiB, nên chừa nhiều GB trống.
- Có BOT_TOKEN, TELEGRAM_API_ID và TELEGRAM_API_HASH. Lấy API ID/hash tại https://my.telegram.org.
- Không gửi token hoặc API hash vào chat, không commit file .env lên GitHub.

## Cấu hình lần đầu

```sh
cp .env.example .env
```

Điền ba giá trị bí mật vào .env trên máy chủ. Không chia sẻ file này.

Trước khi chuyển bot hiện tại từ Telegram Bot API mặc định sang Local Bot API, phải dừng worker cũ và gọi `logOut` trên Bot API mặc định theo hướng dẫn tại [telegram-local-api.md](telegram-local-api.md). Không chạy hai polling worker cho cùng một bot.

## Mỗi lần cần ghép mod

Từ thư mục dự án trên máy chủ:

```sh
sh scripts/start-aki-session.sh
```

Chờ lệnh báo các container đang chạy, sau đó mở Telegram:

1. Gửi ZIP mod dưới dạng Tệp (Document), không gửi dạng ảnh/video.
2. Gửi đủ các ZIP cần ghép.
3. Dùng `/status` để xem số file đã nhận.
4. Dùng `/inspect` để xem định dạng và xung đột đường dẫn.
5. Dùng `/aovmerge` cho đúng cặp 10 skin + Vấn Tâm Kiếm đã hỗ trợ; dùng `/merge` chỉ cho ZIP thông thường không có xung đột đường dẫn.

Khi bot đã gửi file kết quả và không cần thử lại:

```sh
sh scripts/stop-aki-session.sh
```

Lệnh dừng xóa container và network nhưng **không xóa volume dữ liệu Local Bot API**. Không thêm `-v` nếu muốn giữ volume.

## Giới hạn xử lý hiện tại

- Local Bot API cho phép tải file lớn hơn Bot API mặc định; cấu hình bot của dự án đặt tối đa 500 MiB mỗi ZIP, tổng đầu vào 500 MiB, tối đa 10 ZIP.
- ZIP đầu ra của bộ ghép thông thường được giới hạn 1 GiB trong code hiện tại.
- Nhận diện nhiều định dạng chỉ là bước kiểm kê. Bộ ghép chuyên sâu hiện vẫn chỉ xử lý cặp phiên bản 1.64.1.12 + Vấn Tâm Kiếm 1.64.1.11 đã được lập trình riêng.
- Với mod khác, không được coi việc ghép ZIP thông thường là đã hợp nhất nội dung bên trong BattleBank, LobbyBank, AssetBundle, IFix hoặc SoundBank. Đường dẫn trùng có nội dung khác nhau sẽ bị từ chối để tránh ghi đè âm thầm.

## Lưu ý về thời điểm tắt

Không tắt dịch vụ khi bot còn đang tải file, ghép hoặc gửi ZIP. Chỉ dừng sau khi Telegram báo đã gửi kết quả thành công. Nếu dừng sớm, tiến trình và dữ liệu tạm trong container có thể mất; hãy gửi lại file và chạy lại phiên.
