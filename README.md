# Aki AOV Tools

Công cụ web và Telegram bot riêng tư bằng tiếng Việt để tải, kiểm tra cấu trúc, đối chiếu tương thích và xử lý các gói tài nguyên Liên Quân iOS theo adapter đã xác minh.

## Tính năng
- Web UI tiếng Việt, dùng được trên điện thoại.
- Web: tải tối đa 10 ZIP theo khối 4 MiB có SHA-256 từng khối, truyền tối đa hai file song song, khôi phục phiên tải và hiển thị tiến trình; kiểm tra cấu trúc, lập manifest và đối chiếu xung đột; ZIP thường chỉ ghép khi không có xung đột.
- Web API `/api/inspect`: liệt kê phần mở rộng, tên có dấu hiệu là tài nguyên game và so sánh SHA-256 các đường dẫn trùng để phân biệt nội dung giống hệt với xung đột thật (tối đa 80 đường dẫn trùng được hiển thị chi tiết).
- Web API `/api/capabilities`: công bố registry adapter, phân biệt định dạng chỉ kiểm tra với định dạng có bộ ghép đã xác minh; mặc định fail-closed.
- Web API `POST /api/upload/{upload_id}/aov-preflight`: phân loại từng ZIP AoV theo vai trò (MOD wrapper, VTK/Actor 595, KPM100, chưa phân loại), trích Actor ID từ tên AssetBundle và liệt kê blocker của adapter. Chỉ đọc, giữ phiên upload; không tạo ZIP và không cấp quyền ghép cho nhiều MOD wrapper.
- AOV preflight còn lập manifest một tầng `Resources.zip`, nhóm các tài nguyên theo đường dẫn chuẩn hóa và báo SHA-256 duplicate, xung đột payload, phiên bản/platform và dependency AssetBundle; chi tiết chỉ mang tính tư vấn và không cấp quyền ghi/ghép.
- Danh sách xung đột có gợi ý ưu tiên theo đường dẫn (billboard/thông báo hạ gục, hiệu ứng/hoạt ảnh/biến về, UI/nút bấm, âm thanh); đây là heuristic sắp xếp báo cáo, không phải nhận diện chức năng runtime.
- Bộ kiểm tra `/inspect` và `/api/inspect` đọc chữ ký 32 byte đầu mỗi mục để phân loại sơ bộ AoV AES/Zstandard, Unity AssetBundle, ZIP lồng, Wwise, ảnh, XML/JSON và dữ liệu chưa nhận diện; chỉ đọc, không sửa tài nguyên.
- Web API `/api/drive/import`: tải ZIP từ link Google Drive công khai trực tiếp về máy chủ, tối đa 10 link và tổng 500 MB.
- Web API `/api/upload/{upload_id}/aov-merge`: tự chọn adapter theo cấu trúc ZIP; hỗ trợ hai đường ghép có giới hạn: KPM100 ZIP trực tiếp + VTK, hoặc MOD wrapper 10 skin + các ZIP phần VTK iOS.
- Web API `/api/upload/{upload_id}/decode-bytes`: giải mã các tài nguyên `.bytes` AoV AES/Zstandard đã nhận diện từ ZIP đã tải lên; hỗ trợ ZIP `Resources.zip` lồng nhau và bung tiếp ZIP bên trong `.pkg.bytes` đã giải mã (tối đa hai tầng), xuất ZIP riêng cùng `decode-report.json`, không sửa ZIP gốc và không ghép tài nguyên.
- Web API `/api/upload/{upload_id}/notice-scan`: quét chỉ đọc các chuỗi liên quan thông báo trong Lua/XML/JSON/TXT/config/CSV và `.bytes` đã giải mã; trả đường dẫn, từ khóa, đoạn trích ngắn; giữ nguyên phiên tải và file gốc.

## Telegram bot riêng tư

Bot dùng chung pipeline phân loại và ghép AoV với web; không tự ghép ZIP AoV chưa có adapter. Các lệnh hiện có:

- `/start`, `/status`, `/files`, `/clear`: trạng thái và quản lý các ZIP trong phiên.
- `/inspect`: báo cáo cấu trúc ZIP và xung đột.
- `/preflight`: phân loại bộ MOD AoV, kiểm tra adapter và xuất báo cáo JSON chỉ đọc.
- `/notice-scan`: dò ứng viên thông báo khởi động trong tài nguyên đã giải mã; chỉ đọc, không tự xóa popup.
- `/decode`: giải mã tài nguyên `.bytes` có định dạng hỗ trợ và gửi ZIP kết quả riêng.
- `/merge`: dùng adapter AoV đã xác minh nếu bộ đầu vào đủ điều kiện; bộ chưa hỗ trợ sẽ nhận blocker thay vì ZIP ghép thường.

Cần cấu hình `BOT_TOKEN` và `ALLOWED_USER_IDS`. Bot API Telegram mặc định nhận file tối đa 20 MB; để tải file trực tiếp lớn hơn qua Telegram cần Local Bot API Server cùng `TELEGRAM_API_BASE_URL` và `TELEGRAM_FILE_BASE_URL`. Link HTTPS tải ZIP trực tiếp vẫn có giới hạn tổng 500 MB theo cấu hình bot. Giới hạn gửi file kết quả của Bot API mặc định là 50 MB.

## Giải mã tài nguyên AoV `.bytes` trên web

Chọn ZIP rồi bấm **Giải mã tài nguyên .bytes đã hỗ trợ**. Web gọi bộ giải mã AES-CBC/Zstandard hiện có, nhận diện theo header và tên tài nguyên, giải nén các mục `.bytes` hỗ trợ ra một ZIP riêng. Có thể đọc ZIP `Resources.zip` lồng nhau và tiếp tục bung các package `.pkg.bytes` sau giải mã nếu payload bên trong là ZIP, tối đa hai tầng lồng. ZIP nguồn không bị thay đổi; báo cáo `decode-report.json` ghi chế độ giải mã, kích thước, file chưa hỗ trợ và đường dẫn đầu ra. Payload MSES được phân tích thêm version, số field, số row, trạng thái checksum, inventory prefix 4 byte đầu từng row (giới hạn 4.096 row), kích thước row, marker `Hero_LiXin_Skin_C` và prefix trùng; prefix được ghi rõ là dữ liệu thô, không khẳng định đó là khóa ngữ nghĩa; `HeroSkin_1/2.bytes` được thử parse IFix để báo số FixInfo/method/type/string/field; payload `.pkg.bytes` dạng ZIP được bung tiếp và ghi số entry.

Bộ lõi MSES v7 có parser, serializer, kiểm tra checksum và ghép theo khóa. Mọi khóa row mới phải nằm trong allowlist riêng của bảng. Soundbank chỉ nhận row có marker `Hero_LiXin_Skin_C`; `ResSkinSeniorLabelCfg` chỉ nhận key 59500 và từ chối nếu key này đã tồn tại với payload khác; `skillcombine` chỉ nhận đúng năm key Actor 595 đã xác minh (0x000915A7–0x000915AB). Không nối byte hoặc ghi đè bảng khi thiếu quy tắc theo schema.

Giới hạn: tối đa 20.000 entry, 1 GiB dữ liệu giải nén, 256 MiB mỗi entry và 256 MiB kích thước giải mã khai báo cho mỗi tài nguyên. Chức năng này chỉ trích xuất payload đã giải mã để kiểm tra/phân tích; không sửa MSES/IFix, không giải mã Unity AssetBundle hoặc Wwise BNK, không ghép skin và không tạo ZIP có thể cài vào game.

## Quét thông báo khởi động (chỉ đọc)

Trên web dùng nút **Quét thông báo khởi động**; sau khi quét, có thể tải riêng `aki-aov-notice-scan-report.json`. Bộ quét chỉ đọc, giải mã các `.bytes` đã hỗ trợ, bung ZIP package lồng nhau và dò từ khóa trong Lua/XML/JSON/TXT/INI/CFG/CSV cùng metadata object của UnityFS AssetBundle (MonoBehaviour, TextAsset, ScriptableObject; giới hạn 64 MiB/bundle và 500 object). Chế độ quét không tạo ZIP tạm, giới hạn 256 MiB dữ liệu đã giải mã và giữ nguyên ZIP nguồn.

Đây là quét heuristic để tìm ứng viên, không chứng minh chuỗi tìm được là popup đang hiển thị. Bundle vượt giới hạn, payload nhị phân opaque và định dạng không giải mã được có thể bị bỏ qua; báo cáo nêu rõ số AssetBundle đã quét/bỏ qua và lý do. Không tự xóa hoặc ghi đè tài nguyên chỉ vì một từ khóa xuất hiện. Cần đối chiếu object/script và kiểm tra trong game trước khi tạo bản sửa.
## Bộ ghép KPM100 iOS + Vấn Tâm Kiếm VTK

Adapter này nhận đúng KPM100 iOS MOD 1.64.1.12_KPM dạng ZIP trực tiếp (cây `Resources/1.64.1/`, 70 `KPM_MOD_*` payload) cùng VTK iOS Vấn Tâm Kiếm Actor 595 phiên bản 1.64.1.11. Bộ xử lý kiểm tra hash và cấu trúc của cặp nguồn KPM100 đã được tích hợp riêng; không nhận ZIP khác chỉ vì tên file giống nhau.

## Phân loại bộ nguồn nhiều MOD trước khi ghép

Chọn các ZIP 9 skin, 10 skin, Tamyn và các phần VTK rồi bấm **Phân loại bộ MOD + VTK trước khi ghép (chỉ đọc)**. API `/api/upload/{upload_id}/aov-preflight` báo vai trò từng file, số wrapper `Resources.zip`/`DanhSachSkin.txt`, Actor ID phát hiện trong tên AssetBundle và adapter hiện có có phù hợp hay không. Phiên upload được giữ lại để tiếp tục đối chiếu xung đột.

Đây là bước nhận diện, không phải bộ ghép nhiều MOD. Hiện adapter wrapper chỉ nhận đúng một MOD nền 10 skin cùng VTK Actor 595 phiên bản đã ghim. Khi có nhiều wrapper (ví dụ 9 skin + 10 skin), Tamyn/Actor khác hoặc xung đột chưa có parser/serializer tương ứng, web sẽ báo blocker và không tự chọn nền, ghi đè hay xuất ZIP. Cần hoàn tất adapter riêng cho từng loại tài nguyên và fixture kiểm thử trước khi bật ghép nhiều MOD.

Báo cáo đối chiếu xung đột còn gắn **gợi ý adapter theo đúng đường dẫn**: CommonActions XML/track (hỗ trợ có giới hạn), IFix và MSES (chỉ theo adapter/allowlist đã khóa), ZIP lồng KernelLua/StableSystems (chỉ union path không xung đột), Packer/Verification metadata (chỉ VTK adapter hiện tại), Unity AssetBundle và Wwise BNK (inspect-only), AssetRef/package actor (cần parser và kiểm tra dependency). Đây là hướng xử lý tiếp theo, không cấp quyền ghép và không đồng nghĩa các parser hiện có tương thích mọi MOD.

Sau khi chạy preflight, nút **Tải báo cáo phân loại (.json)** cho phép lưu đầy đủ kết quả nguồn, Actor ID, blocker, kiểm kê xung đột và gợi ý adapter để chia sẻ hoặc lưu trên iPhone. JSON này chỉ là báo cáo, không chứa ZIP nguồn hay cấp quyền ghép.

### Nhận diện bộ nguồn P9 + P10 + TM + VTK

Web preflight cũng hiển thị riêng bộ mục tiêu gồm đúng sáu ZIP: P9 (9 skin, camera xa 30%), P10 (10 skin, camera xa 30%), TM Tamyn - Robot, VTK Part 1, Part 2 và Part 3. Nhãn P9/P10/TM có thể dựa trên tên file hoặc dấu hiệu Actor 593; ba phần VTK chỉ được nhóm khi tên Part 1–3 đi kèm bằng chứng Actor 595 và HeroSkin_2 trong cả bộ đầu vào. Báo cáo nêu rõ cơ sở nhận diện và file còn thiếu/trùng vai trò.

**Quan trọng:** đây mới là cập nhật nhận diện/preflight. Nó không bật quyền ghép P9 + P10 + TM + VTK. Inventory đã ghi nhận xung đột ở CommonActions, IFix, MSES, KernelLua/StableSystems, âm thanh và metadata; mỗi định dạng cần adapter semantic riêng, round-trip test và fixture thực tế. Khi chưa có các quy tắc đó, web tiếp tục fail-closed và không xuất ZIP nhiều MOD có nguy cơ làm mất hiệu ứng, biến về, thông báo hạ, nút bấm hoặc âm thanh.

## Bộ ghép MOD wrapper 10 skin + Vấn Tâm Kiếm VTK

Adapter này nhận một ZIP MOD wrapper có đúng một `Resources.zip` và một `DanhSachSkin.txt`, cộng với một hoặc nhiều ZIP phần VTK (tối đa 10 ZIP tổng). `Resources.zip` bên trong phải có các tài nguyên nền đúng schema iOS 1.64.1.12; bộ VTK phải đúng phiên bản 1.64.1.11 và có đủ chính xác 10 AssetBundle Actor 595 bắt buộc. VTK chia thành Part1/Part2/Part3 được hỗ trợ; đường dẫn được chuẩn hóa trước khi ghép và các file trùng giữa các phần chỉ được bỏ qua khi nội dung giống hệt.

Bộ ghép không thay cả file trùng một cách mù quáng. Nó dùng adapter riêng cho IFix, MSES v7 theo allowlist, CommonActions XML/track, ResourcePackerInfoSet, ResourceVerificationInfoSet, AssetBundle và sound iOS. Với `KernelLua.pkg.bytes` và `StableSystems_3.pkg.bytes`, payload ZIP lồng được hợp nhất theo quy tắc giữ mục riêng, bỏ duplicate chỉ khi SHA-256 giống hệt; nếu một đường dẫn bên trong trùng tên nhưng khác nội dung thì dừng thay vì chọn ngẫu nhiên. Nó kiểm tra CRC ZIP đầu vào/đầu ra, checksum bảng, inventory đường dẫn nền và metadata phụ thuộc; thiếu/thừa AssetBundle, sai phiên bản, checksum không hợp lệ hoặc xung đột chưa có quy tắc sẽ dừng và không xuất file hoàn chỉnh. Không cần đưa ZIP wrapper lên GitHub Release; có thể tải trực tiếp các ZIP nguồn lên web.

Trên bộ nguồn 10 skin + ba phần VTK đã được tích hợp trước đó, báo cáo ghi nhận ZIP ứng viên 150,793,052 byte, giữ 59/59 đường dẫn nền, thêm 24 tài nguyên VTK, 10/10 AssetBundle Actor 595 có thể đọc và các bước IFix, CommonActions, MSES, Packer/Verification đều qua kiểm tra cấu trúc. Đây là **xác minh cấu trúc**, không phải test trong Liên Quân iOS. Hiệu ứng, biến về, thông báo hạ, nút bấm, âm thanh và khả năng vào trận vẫn phải thử trên thiết bị; không có cam kết tương thích 100% trước bước đó.

**Giới hạn chung:** chỉ hỗ trợ đúng schema iOS 1.64.1.12 + VTK 1.64.1.11 và bộ Actor 595 nêu trên. Không tải, không yêu cầu và không chèn tài nguyên từ bộ cài game gốc. Các ZIP AoV khác tiếp tục bị từ chối hoặc chỉ kiểm kê cho tới khi có adapter riêng.

## Phạm vi kiểm tra Wwise BNK

Bộ đọc BNK hiện kiểm tra chunk, DIDX/DATA, hash media và inventory HIRC. Phần framing HIRC chọn độ rộng trường object type theo bank version: 4 byte với version <= 48 và 1 byte từ version 49. Payload của từng loại HIRC vẫn được giữ opaque; chưa giải mã đồ thị Event/Action/Container, chưa giải quyết tham chiếu WEM/HIRC và chưa có serializer. So sánh BNK chỉ mang tính tư vấn, không cấp quyền ghép hoặc thay thế SoundBank.

## Chạy web
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Mở http://localhost:8000

## Nhập ZIP từ Google Drive
- Trên web, để ghép MOD wrapper 10 skin, chọn ZIP có `Resources.zip` + `DanhSachSkin.txt` cùng các ZIP phần VTK iOS (Part1/Part2/Part3). Hoặc chọn KPM100 ZIP trực tiếp + VTK. Máy chủ tự nhận dạng adapter; cả hai chỉ nhận đúng phiên bản/schema.
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
- Chỉ hỗ trợ hai cặp nguồn/schema và phiên bản nêu trên. Không tự áp dụng cho bản game/skin khác.

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
