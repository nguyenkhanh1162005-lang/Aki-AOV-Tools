# Kiến trúc bộ xử lý đa dạng mod AoV

## Mục tiêu

Tạo pipeline ghép nhiều gói mod AoV/iOS, bao gồm mod AoV gốc và gói tài nguyên đã được chuyển đổi từ trò chơi khác (ví dụ HoK -> AoV). Không được coi việc cùng đuôi tệp hoặc cùng đường dẫn là bằng chứng tương thích.

Pipeline phải hoạt động theo nguyên tắc **inspect -> classify -> parse -> plan -> merge -> validate -> package**. Nếu một bước chưa có parser/serializer hoặc quy tắc tương thích đã kiểm chứng, pipeline phải từ chối ghép phần tài nguyên đó và nêu rõ lý do; không âm thầm ghi đè.

## Các lớp kiến trúc

1. **Archive intake**
   - ZIP outer/nested ZIP, giới hạn số entry, tổng dung lượng giải nén, path traversal, symlink, ZIP bomb, CRC.
   - Giữ file gốc bất biến; làm việc trên bản sao trong thư mục job riêng.
   - Ghi SHA-256 cho từng input và output.

2. **Format detection**
   - Phát hiện theo magic bytes và cấu trúc nội dung, không chỉ extension.
   - Nhận diện AoV AES wrapper, Zstandard, MSES, Unity AssetBundle, Wwise BNK, XML/JSON, hình ảnh và dữ liệu chưa biết.
   - Kết quả detection là metadata, không phải tuyên bố giải mã hay tương thích.

3. **Resource manifest**
   - Mỗi resource có: archive nguồn, path chuẩn hóa, size, CRC32, SHA-256, format, game version, platform, actor/skin ID nếu xác định được, dependencies và confidence.
   - Chuẩn hóa prefix Resources/<version>/ nhưng phải giữ lại đường dẫn gốc để tái đóng gói.

4. **Adapter registry**
   - `zip_generic`: chỉ ghép tệp không xung đột hoặc byte-identical.
   - `aov_mses`: parser/serializer theo schema/version cụ thể; kiểm tra field count, row count, index, checksum.
   - `aov_ifix`: parser/serializer IFix theo version.
   - `aov_common_actions`: xử lý XML/Zstandard và track/action IDs.
   - `unity_assetbundle`: inspect UnityFS và dependency; chỉ merge/rewrite khi adapter hỗ trợ chính xác loại bundle/version.
   - `wwise_bank`: inspect BKHD/DIDX/DATA và media IDs; không nối byte các bank.
   - `aov_metadata`: cập nhật PackerInfo/ResourceVerificationInfo cùng resource thay đổi.
   - `cross_game_conversion`: chỉ nhận gói đã được chuyển đổi sang schema AoV; không giả định asset HoK có thể đổi tên là chạy được.

5. **Compatibility planner**
   - Kiểm tra game build, platform iOS/Android, actor/skin ID, resource schema, bundle version, dependency graph và xung đột.
   - Phân loại: SAFE_MERGE, IDENTICAL_DUPLICATE, NEEDS_RULE, UNSUPPORTED, VERSION_CONFLICT.
   - Với xung đột khác nội dung, bắt buộc có quy tắc adapter cụ thể hoặc lựa chọn rõ ràng; mặc định không overwrite.

6. **Merge executor**
   - Chỉ gọi adapter sau khi planner xác nhận.
   - Transactional: ghi ra output tạm, không sửa input; lỗi bất kỳ thì hủy output.
   - Có log theo từng resource và báo cáo lý do chọn/loại.

7. **Validation & packaging**
   - CRC test toàn ZIP; parse lại các resource đã serialize; kiểm tra metadata tham chiếu đúng checksum/size; kiểm tra dependency không thiếu.
   - Ghi manifest/report kèm output.
   - Chỉ ghi "structurally validated"; không ghi "game compatible" nếu chưa có test trên đúng build iOS.

## Actor 599 / gói Billow làm mẫu nghiên cứu

Báo cáo inventory hiện có cho thấy 20 file: 12 .bytes, 6 .assetbundle, 1 .bnk, 1 .txt; các path liên quan Actor_599, heroSkin, BattleBank, LobbyBank và Hero_Lan_Skin_C_SFX.bnk. Đây mới là inventory, chưa có payload thực tế.

Các bước phân tích tiếp theo khi có payload:
1. Đọc header/magic và xác định từng lớp wrapper/compression/encryption.
2. Nếu có thể giải mã hợp lệ, parse các bảng định danh Actor/Skin và xác định mapping AoV.
3. Kiểm tra AssetBundle bằng Unity parser phù hợp; trích xuất dependency/object manifest, không tự động ghi lại bundle.
4. Kiểm tra BNK bằng cấu trúc Wwise; đối chiếu media IDs/event references.
5. So sánh resource với một gói AoV cùng build để xác định phần đã chuyển đổi.
6. Chỉ tạo adapter Actor 599 sau khi có fixture đầu vào/đầu ra và test round-trip.

Không suy ra quy tắc chung cho tất cả mod từ một mẫu Actor 599.

## Kiểm thử bắt buộc

- Unit tests cho từng detector/parser/serializer.
- Round-trip: parse -> serialize -> parse và so sánh semantic data.
- Golden fixtures theo từng build/platform.
- Conflict tests: cùng path giống bytes, cùng path khác bytes, dependency thiếu, version lệch.
- ZIP security tests: traversal, encrypted entry, ZIP bomb, symlink, corrupt CRC.
- Integration tests: nhiều mod khác Actor, cùng Actor khác Skin ID, cùng Actor cùng Skin ID, mod HoK-converted + mod AoV-native.
- Large-file tests theo giới hạn input/output cấu hình; không giữ toàn bộ archive trong RAM.
- Telegram end-to-end: upload 500 MB, xử lý, gửi output hoặc link tải; kiểm tra restart và cleanup.

## Tiêu chí phát hành

Không bật adapter mặc định chỉ vì detector nhận diện được format. Mỗi adapter phải có parser, serializer (nếu cần), compatibility rules, tests và fixture. Các adapter đã kiểm chứng được bật riêng; tài nguyên chưa hỗ trợ vẫn được kiểm kê và báo rõ.
