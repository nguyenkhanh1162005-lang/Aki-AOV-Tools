# Xử lý AOV miễn phí bằng Google Drive và GitHub Actions

Đây là chế độ chạy theo từng lượt, không phải bot 24/7.

## Cách hoạt động

1. Người dùng tải các ZIP lên Google Drive bằng iPhone.
2. Chia sẻ tạm thời file hoặc thư mục chứa ZIP ở chế độ **Anyone with the link - Viewer**.
3. Lưu từng link Drive, mỗi link một dòng, vào GitHub Actions Secret có tên `AKI_DRIVE_LINKS`.
4. Chạy workflow **Aki free Drive AOV processor** và nhập `RUN`.
5. Workflow tải các ZIP về runner, gọi đúng engine `merge_aov_archives` hiện có, kiểm tra CRC và SHA-256.
6. Tải artifact `aki-aov-result-...` về iPhone trong vòng 1 ngày.

## Cấu hình một lần

1. Mở repository Settings.
2. Vào **Secrets and variables → Actions**.
3. Chọn **New repository secret**.
4. Name: `AKI_DRIVE_LINKS`.
5. Secret: dán link Google Drive, mỗi ZIP một dòng, theo thứ tự tùy ý. Engine tự nhận diện ZIP nền bằng cấu trúc bên trong.
6. Save.

Không đặt link Drive trực tiếp vào phần input của workflow, issue, pull request hoặc log vì repository là public. Secret giúp giữ link khỏi mã nguồn và metadata hiển thị thông thường của lần chạy. File nguồn vẫn cần được chia sẻ bằng link để runner đọc được; bất kỳ ai có link cũng có thể tải chúng trong thời gian quyền đó còn hiệu lực.

## Chạy trên iPhone

1. Mở tab **Actions** trong GitHub repository.
2. Chọn **Aki free Drive AOV processor**.
3. Chọn **Run workflow**.
4. Nhập chính xác `RUN`, sau đó xác nhận.
5. Mở lần chạy mới nhất. Khi hoàn tất, vào **Artifacts** và tải `aki-aov-result-...`.
6. Lưu ZIP vào ứng dụng Files trên iPhone.

Khi thay bộ ZIP đầu vào, cập nhật secret `AKI_DRIVE_LINKS` trước lần chạy tiếp theo.

## Giới hạn

- Cần 2-10 ZIP, gồm một ZIP nền 10 skin được engine hỗ trợ và ít nhất một ZIP Vấn Tâm Kiếm tương ứng.
- Tổng đầu vào tối đa 500 MiB.
- Chỉ hỗ trợ cặp phiên bản được khóa trong engine hiện tại: base 1.64.1.12 và VTK 1.64.1.11 Actor 595.
- Artifact chỉ giữ 1 ngày để hạn chế dùng dung lượng lưu trữ Actions.
- GitHub Actions có hạn mức sử dụng theo gói và giới hạn thời gian mỗi job; nếu hết quota hoặc runner bị dừng thì lượt xử lý sẽ thất bại.
- File đầu ra được tải từ GitHub Actions, không tự gửi về Telegram và không tự ghi vào Google Drive.
- Kiểm tra CRC ZIP không thay thế cho việc cài và thử trong Liên Quân iOS.
- Workflow chỉ xuất hiện để chạy thủ công sau khi thay đổi được hợp nhất vào nhánh mặc định `main`.

## Bảo mật

- Không lưu bot token, Google OAuth token hoặc dữ liệu đăng nhập trong repository.
- Không dán link Drive vào nơi công khai.
- Sau khi xử lý xong, có thể tắt quyền Anyone with the link trên thư mục nguồn.
- Không tải file không rõ nguồn gốc. Engine kiểm tra cấu trúc nhưng không thể chứng minh mọi nội dung ZIP đều an toàn.
