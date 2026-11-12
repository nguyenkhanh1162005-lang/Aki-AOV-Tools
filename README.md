# Aki AOV Tools

Công cụ web tiếng Việt để tải, kiểm tra cấu trúc, đối chiếu tương thích và xử lý các gói tài nguyên Liên Quân iOS theo adapter đã xác minh. Tài liệu này tập trung vào web; Telegram bot nằm ngoài phạm vi triển khai hiện tại.

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: tải tối đa 10 ZIP theo khối 1 MiB có SHA-256 từng khối, khôi phục phiên tải, kiểm tra cấu trúc, lập manifest và đối chiếu xung đột; ZIP thường chỉ ghép khi không có xung đột.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và so sánh SHA-256 các đường dẫn trùng để phân biệt nội dung giống hệt với xung đột thật (tối đa 80 đường dẫn trùng được hiển thị chi tiết).
- Web API `/api/capabilities`: công bố registry adapter, phân biệt định dạng chỉ kiểm tra với định dạng có bộ ghép đã xác minh; mặc định fail-closed.
- Bộ kiểm tra `/inspect` và `/api/inspect` đọc chữ ký 32 byte đầu mỗi mục để phân loại sơ bộ AoV AES/Zstandard, Unity AssetBundle, ZIP lồng, Wwise, ảnh, XML/JSON và dữ liệu chưa nhận diện; chỉ đọc, không sửa tài nguyên.
- Web API `/api/drive/import`: tải ZIP từ link Google Drive công khai trực tiếp về máy chủ, tối đa 10 link và tổng 500 MB.
- Web API `/api/upload/{upload_id}/aov-merge`: dùng cùng pipeline chọn adapter; hỗ trợ đúng cặp ZIP KPM100 trực tiếp + VTK iOS đã ghim SHA-256.
- Web API `/api/upload/{upload_id}/decode-bytes`: giải mã các tài nguyên `.bytes` AoV AES/Zstandard đã nhận diện từ ZIP đã tải lên; hỗ trợ ZIP `Resources.zip` lồng nhau và bung tiếp ZIP bên trong `.pkg.bytes` đã giải mã (tối đa hai tầng), xuất ZIP riêng cùng `decode-report.json`, không sửa ZIP gốc và không ghép tài nguyên.
- Web API `/api/upload/{upload_id}/notice-scan`: quét chỉ đọc các chuỗi liên quan thông báo trong Lua/XML/JSON/TXT/config/CSV và `.bytes` đã giải mã; trả đường dẫn, từ khóa, đoạn trích ngắn; giữ nguyên phiên tải và file gốc.

## Giải mã tài nguyên AoV `.bytes` trên web

Chọn ZIP rồi bấm **Giải mã tài nguyên .bytes đã hỗ trợ**. Web gọi bộ giải mã AES-CBC/Zstandard hiện có, nhận diện theo header và tên tài nguyên, giải nén các mục `.bytes` hỗ trợ ra một ZIP riêng. Có thể đọc ZIP `Resources.zip` lồng nhau và tiếp tục bung các package `.pkg.bytes` sau giải mã nếu payload bên trong là ZIP, tối đa hai tầng lồng. ZIP nguồn không bị thay đổi; báo cáo `decode-report.json` ghi chế độ giải mã, kích thước, file chưa hỗ trợ và đường dẫn đầu ra. Payload MSES được phân tích thêm version, số field, số row, trạng thái checksum, inventory prefix 4 byte đầu từng row (giới hạn 4.096 row), kích thước row, marker `Hero_LiXin_Skin_C` và prefix trùng; prefix được ghi rõ là dữ liệu thô, không khẳng định đó là khóa ngữ nghĩa; `HeroSkin_1/2.bytes` được thử parse IFix để báo số FixInfo/method/type/string/field; payload `.pkg.bytes` dạng ZIP được bung tiếp và ghi số entry.

Bộ lõi MSES v7 có parser, serializer, kiểm tra checksum và ghép theo khóa. Mọi khóa row mới đều phải nằm trong allowlist cụ thể của bảng; schema khác nhau còn cần bật rõ quyền thêm row cho adapter đó. Soundbank chỉ nhận row có marker `Hero_LiXin_Skin_C`, `ResSkinSeniorLabelCfg` chỉ nhận key 59500 và từ chối nếu key này đã tồn tại với payload khác vì chưa có quy tắc ghi đè đã xác minh; `skillcombine` hiện chưa có allowlist row mới đã xác minh nên sẽ từ chối nếu overlay thêm key. Đây là chốt an toàn, đồng nghĩa bộ ghép Vấn Tâm Kiếm có thể dừng ở bước MSES cho tới khi xác minh được schema/khóa skillcombine thực tế.

Giới hạn: tối đa 20.000 entry, 1 GiB dữ liệu giải nén, 256 MiB mỗi entry và 256 MiB kích thước giải mã khai báo cho mỗi tài nguyên. Chức năng này chỉ trích xuất payload đã giải mã để kiểm tra/phân tích; không sửa MSES/IFix, không giải mã Unity AssetBundle hoặc Wwise BNK, không ghép skin và không tạo ZIP có thể cài vào game.

## Quét thông báo khởi động (chỉ đọc)

Trên web dùng nút **Quét thông báo khởi động**; sau khi quét, có thể tải riêng `aki-aov-notice-scan-report.json`. Bộ quét chỉ đọc, giải mã các `.bytes` đã hỗ trợ, bung ZIP package lồng nhau và dò từ khóa trong Lua/XML/JSON/TXT/INI/CFG/CSV cùng metadata object của UnityFS AssetBundle (MonoBehaviour, TextAsset, ScriptableObject; giới hạn 64 MiB/bundle và 500 object). Chế độ quét không tạo ZIP tạm, giới hạn 256 MiB dữ liệu đã giải mã và giữ nguyên ZIP nguồn.

Đây là quét heuristic để tìm ứng viên, không chứng minh chuỗi tìm được là popup đang hiển thị. Bundle vượt giới hạn, payload nhị phân opaque và định dạng không giải mã được có thể bị bỏ qua; báo cáo nêu rõ số AssetBundle đã quét/bỏ qua và lý do. Không tự xóa hoặc ghi đè tài nguyên chỉ vì một từ khóa xuất hiện. Cần đối chiếu object/script và kiểm tra trong game trước khi tạo bản sửa.
## Bộ ghép KPM100 iOS + Vấn Tâm Kiếm VTK

**Adapter đang bật có giới hạn phiên bản** cho đúng hai archive nguồn đã kiểm tra:
- KPM100 iOS MOD 1.64.1.12_KPM, ZIP trực tiếp chứa cây `Resources/1.64.1/` và đúng 70 `KPM_MOD_*` payload.
- VTK iOS Vấn Tâm Kiếm Actor 595, dữ liệu 1.64.1.11.

Bộ xử lý ghim SHA-256 của cả hai archive. Không nhận ZIP khác hash, sai phiên bản hoặc sai cấu trúc chỉ vì tên file giống nhau. KPM100 là ZIP resource tree trực tiếp, không phải wrapper ngoài có `Resources.zip` và `DanhSachSkin.txt`. Adapter wrapper 10 skin khác vẫn bị khóa cho tới khi có bài tích hợp riêng.
Để kiểm tra đúng wrapper 10 skin của người dùng, workflow **10-skin wrapper + VTK source audit** đang chờ asset `IOS-Pack-10Skin-Cam30.zip` trong release [`aov-upload-01`](https://github.com/nguyenkhanh1162005-lang/Aki-AOV-Tools/releases/tag/aov-upload-01). Release hiện có KPM100 trực tiếp và `VTK.IOS.VIP.zip`, chưa có wrapper 10 skin. Sau khi tải wrapper ZIP lên release với đúng tên asset trên, chạy workflow này trong GitHub Actions. Workflow sẽ quét chuỗi thông báo, chạy ghép thử trên nguồn thật, kiểm tra CRC và bảo toàn đường dẫn nền, rồi lưu báo cáo cùng ZIP ứng viên. Workflow không tự mở khóa adapter; cần ghim SHA-256 và xác nhận toàn bộ kiểm tra trước.


Trong cặp nguồn được hỗ trợ, bộ ghép:
- Giữ nguyên mọi đường dẫn của KPM100 và chỉ thay payload liên quan Actor 595; báo cáo xác nhận toàn bộ đường dẫn gốc vẫn còn.
- Thay 68 XML kỹ năng cùng một AssetRef và một ActorInfo trong 70 payload KPM_MOD; kiểm tra round-trip của 70 payload và parse 68 XML.
- Ghép IFix của `Thanos/HeroSkin_2.bytes` vào `HeroSkin_1.bytes`, remap chỉ số và kiểm tra parse/serialize sau mã hóa.
- Ghép các bảng MSES có allowlist theo từng bảng, CommonActions theo track, cập nhật ResourcePacker/ResourceVerification và các dependency AssetBundle của Actor 595.
- Thêm các AssetBundle và soundbank iOS VTK được liệt kê trong báo cáo kiểm tra.

Bài tích hợp trên đúng hai archive đã ghim đã thành công: CRC ZIP ngoài, IFix round-trip, 70/70 payload, 68/68 XML kỹ năng, Actor Actions package 129 entry CRC và ActorInfo package CRC đều đạt. Đây là **xác minh cấu trúc**, không phải thử nghiệm chạy trong Liên Quân iOS. Chưa có xác nhận rằng model, hoạt ảnh, hiệu ứng, thông báo hạ, biến về, nút bấm và âm thanh đều hiển thị/phát đúng trong trận; cần sao lưu bản gốc và thử trên thiết bị trước khi sử dụng.

**Quy tắc nguồn:** chỉ ghép đúng MOD KPM100 với MOD VTK đã ghim; không tải, không yêu cầu và không chèn tài nguyên từ bộ cài game gốc. Những ZIP AoV khác tiếp tục bị từ chối hoặc chỉ kiểm kê cho tới khi có adapter riêng.

## Phạm vi kiểm tra Wwise BNK

Bộ đọc BNK hiện kiểm tra chunk, DIDX/DATA, hash media và inventory HIRC. Phần framing HIRC chọn độ rộng trường object type theo bank version: 4 byte với version <= 48 và 1 byte từ version 49. Payload của từng loại HIRC vẫn được giữ opaque; chưa giải mã đồ thị Event/Action/Container, chưa giải quyết tham chiếu WEM/HIRC và chưa có serializer. So sánh BNK chỉ mang tính tư vấn, không cấp quyền ghép hoặc thay thế SoundBank.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Nhập ZIP từ Google Drive
- Trên web, để ghép AOV, dùng đúng 2 ZIP KPM100 iOS 1.64.1.12 và VTK iOS 1.64.1.11 đã ghim SHA-256. Wrapper MOD 10 skin có `Resources.zip` + `DanhSachSkin.txt` vẫn bị khóa; không gửi nhầm bộ đó để ghép.
- File phải bật quyền “Bất kỳ ai có đường liên kết”.
- Chỉ nhận link `drive.google.com` hoặc `docs.google.com`; máy chủ tự tải file, giới hạn 500 MB mỗi ZIP và tổng 500 MB.
- Sau khi tải và kiểm tra, dùng nút ghép AOV chuyên sâu trước khi phiên upload hết hạn (1 giờ).

## Triển khai web
- Build bằng Dockerfile hiện có; ứng dụng FastAPI lắng nghe cổng `8000`.
- Health check: `GET /health`.
- Kiểm tra phạm vi adapter trước khi dùng: `GET /api/capabilities`.
- Phiên tải mặc định nằm trên filesystem tạm và bộ nhớ tiến trình. Nếu bật persistent disk, đặt `AOV_UPLOAD_STORAGE_DIR` theo mount path có quyền ghi; ứng dụng hiện yêu cầu một Uvicorn worker để giữ session nhất quán.
- Không bật adapter chỉ dựa vào CI xanh. `game_tested` chỉ được true sau khi có xác nhận thử nghiệm thật trong Liên Quân iOS.

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

Với ZIP dạng wrapper có chứa `Resources.zip`, manifest kiểm tra thêm đúng một tầng ZIP lồng nhau ở chế độ chỉ đọc. Báo cáo giữ `container_path` để phân biệt tệp trong ZIP ngoài và tài nguyên bên trong, đồng thời tính giới hạn số entry/dung lượng trên cả hai tầng. ZIP lồng sâu hơn không được tự động bung.

Manifest cũng suy luận phiên bản game/nền tảng từ tên ZIP khi đường dẫn tài nguyên bên trong không chứa metadata đó. Compatibility planner tổng hợp phiên bản và nền tảng trên toàn bộ từng ZIP đầu vào, không chỉ các đường dẫn bị trùng; vì vậy hai ZIP có phiên bản iOS khác nhau vẫn được cảnh báo dù chúng chứa các đường dẫn tài nguyên riêng biệt. Đây là cảnh báo dựa trên tên archive/đường dẫn, không phải xác thực phiên bản nhị phân; nếu không tìm thấy metadata thì báo cáo ghi không xác định và không tự kết luận tương thích.

Bộ kiểm tra Wwise BNK đọc tối đa 40 SoundBank mỗi lượt, mỗi file không quá 64 MiB. Ngoài so sánh các file trùng đường dẫn, báo cáo còn nhóm theo `BKHD Bank ID` để phát hiện các bản sao cùng identity dù tên file khác nhau; nếu cùng Bank ID nhưng payload khác, công cụ đối chiếu SHA-256 của media `DIDX/DATA` và HIRC object payload để đánh dấu `NEEDS_RULE`. Đây chỉ là inventory/xung đột identity: chưa giải mã HIRC reference graph, Event/Action semantics hoặc tạo SoundBank mới, và không cấp quyền ghép.


### Persistent upload sessions (optional)

By default, upload sessions and temporary ZIP files live in the instance's temporary filesystem and in-memory session registry. To restore interrupted uploads after an application restart, set `AOV_UPLOAD_STORAGE_DIR` to a writable persistent mount path. The application writes each upload session's metadata atomically beside its uploaded ZIP chunks and validates the files before restoring a session. The web service must run with a single Uvicorn worker: active session state is held in that worker's memory, so multiple worker processes would not share upload IDs or chunk offsets. The repository Dockerfile starts one worker by default; do not add `--workers` unless session state is moved to a shared backend.

For a Render service using a persistent disk, a typical configuration is:
- Disk mount path: `/var/data`
- Environment variable: `AOV_UPLOAD_STORAGE_DIR=/var/data/aki-aov-uploads`

Render persistent disks require a paid service and restrict the service to one instance; they also disable zero-downtime deploys. Leave the variable unset to retain the existing ephemeral-storage behavior. A persistent disk does not provide shared storage for multiple service instances. See the [Render persistent disk documentation](https://render.com/docs/disks).
