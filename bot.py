import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from telegram import Update, FSInputFile
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.large_merger import merge_zip_files, MergeError
from app.aov_inspector import inspect_archives, InspectError
from app.url_downloader import download_zip_from_url, DownloadError
from app.access_control import is_allowed_user
from app.aov_bytes_archive import AOVBytesArchiveError, decode_archive_resources
from app.mod_pipeline import (
    ModPipelineError,
    analyze_aov_input_set,
    merge_mod_archives,
)

MIB = 1024 * 1024
LOCAL_API = bool(
    os.environ.get("TELEGRAM_API_BASE_URL")
    and os.environ.get("TELEGRAM_FILE_BASE_URL")
)
MAX_FILE_BYTES = 500 * MIB if LOCAL_API else 20 * MIB
MAX_TOTAL_BYTES = 500 * MIB if LOCAL_API else 100 * MIB
MAX_FILES = 10


async def _authorized(update: Update) -> bool:
    user = update.effective_user
    message = update.effective_message
    allowed_ids = os.environ.get("ALLOWED_USER_IDS", "")
    if user and is_allowed_user(user.id, allowed_ids):
        return True
    if message:
        await message.reply_text("Bot đang ở chế độ riêng tư. Tài khoản này chưa được cấp quyền sử dụng.")
    return False


def _workdir(context: ContextTypes.DEFAULT_TYPE) -> Path:
    path = context.user_data.get("workdir")
    if not path:
        path = tempfile.mkdtemp(prefix="aki-aov-")
        context.user_data["workdir"] = path
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    path = context.user_data.pop("workdir", None)
    context.user_data.pop("zips", None)
    if path:
        shutil.rmtree(path, ignore_errors=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    _cleanup(context)
    mode = "500 MB/file, tổng 500 MB" if LOCAL_API else "20 MB/file (Bot API mặc định)"
    await update.message.reply_text(
        "Aki-AOV Bot đã sẵn sàng.\n"
        f"Chế độ nhận file: {mode}.\n\n"
        "Gửi tối đa 10 ZIP hoặc gửi link HTTPS tải ZIP trực tiếp, sau đó dùng /inspect để phân tích hoặc /merge để ghép.\n"
        "/status xem giới hạn hiện tại\n"
        "/files xem danh sách ZIP đã nhận\n"
        "/inspect xuất báo cáo cấu trúc ZIP dạng TXT\n"
        "/preflight phân loại MOD AoV và kiểm tra adapter trước khi ghép\n"
        "/notice-scan dò ứng viên thông báo khởi động (chỉ đọc, không xóa)\n"
        "/decode giải mã tài nguyên .bytes được hỗ trợ thành ZIP\n"
        "/merge kiểm tra, ghép và xác minh lại dữ liệu đầu ra\n"
        "/clear xóa danh sách file"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    total = sum(Path(path).stat().st_size for _, path in files if Path(path).exists())
    mode = "Local Bot API" if LOCAL_API else "Bot API mặc định"
    await update.message.reply_text(
        f"Chế độ: {mode}\n"
        f"ZIP gửi trực tiếp qua Telegram: {MAX_FILE_BYTES // MIB} MB/file\n"
        "ZIP tải từ link HTTPS trực tiếp: tối đa 500 MB tổng\n"
        "Giới hạn gửi ZIP kết quả qua Bot API mặc định: 50 MB\n"
        f"Đã nhận: {len(files)}/{MAX_FILES} file, {total / MIB:.1f} MB"
    )


async def files_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return
    lines = ["DANH SÁCH ZIP ĐÃ NHẬN"]
    for index, (name, path) in enumerate(files, 1):
        size = Path(path).stat().st_size if Path(path).exists() else 0
        lines.append(f"{index}. {name} — {size / MIB:.1f} MB")
    await update.effective_message.reply_text("\n".join(lines))


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    message = update.effective_message
    doc = message.document if message else None
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.reply_text("Hiện tại bot nhận ZIP để phân tích và ghép. Hãy gửi file .zip dưới dạng Tệp nhé.")
        return

    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        if not LOCAL_API:
            reason = (
                "Bot đang dùng Bot API mặc định. Muốn nhận ZIP 500 MB, "
                "cần kết nối Local Bot API Server trước."
            )
        else:
            reason = "File vượt quá giới hạn 500 MB hiện tại."
        await message.reply_text(reason)
        return

    files = context.user_data.setdefault("zips", [])
    if len(files) >= MAX_FILES:
        await message.reply_text("Đã đủ 10 file. Dùng /merge hoặc /clear.")
        return

    current_total = sum(
        Path(path).stat().st_size for _, path in files if Path(path).exists()
    )
    if doc.file_size and current_total + doc.file_size > MAX_TOTAL_BYTES:
        await message.reply_text(
            f"Tổng dung lượng tối đa là {MAX_TOTAL_BYTES // MIB} MB."
        )
        return

    folder = _workdir(context)
    safe_name = Path(doc.file_name).name
    destination = folder / f"{len(files) + 1}_{safe_name}"
    status_message = await message.reply_text(
        f"Đang tải {safe_name}. File lớn có thể mất vài phút..."
    )
    try:
        telegram_file = await doc.get_file()
        await telegram_file.download_to_drive(custom_path=destination)
        actual_size = destination.stat().st_size
        if actual_size > MAX_FILE_BYTES or current_total + actual_size > MAX_TOTAL_BYTES:
            destination.unlink(missing_ok=True)
            await status_message.edit_text("File vượt quá giới hạn dung lượng cho phép.")
            return
        files.append((safe_name, str(destination)))
        await status_message.edit_text(
            f"Đã nhận {len(files)}/{MAX_FILES} file: {safe_name} "
            f"({actual_size / MIB:.1f} MB)"
        )
    except Exception as exc:
        destination.unlink(missing_ok=True)
        await status_message.edit_text(
            f"Tải file thất bại: {type(exc).__name__}. Thử gửi lại nhé."
        )


async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    message = update.effective_message
    user = update.effective_user
    allowed_ids = os.environ.get("ALLOWED_USER_IDS", "")
    if not user or not is_allowed_user(user.id, allowed_ids):
        await message.reply_text(
            "Chức năng tải ZIP qua liên kết hiện chưa được bật cho tài khoản này."
        )
        return
    url = (message.text or "").strip()
    if not url.startswith("https://"):
        await message.reply_text(
            "Gửi một liên kết HTTPS tải trực tiếp tới file .zip. "
            "Liên kết trang xem trước (không phải link tải trực tiếp) có thể không dùng được."
        )
        return

    files = context.user_data.setdefault("zips", [])
    if len(files) >= MAX_FILES:
        await message.reply_text("Đã đủ 10 file. Dùng /merge hoặc /clear.")
        return
    current_total = sum(
        Path(path).stat().st_size for _, path in files if Path(path).exists()
    )
    link_limit = 500 * MIB
    if current_total >= link_limit:
        await message.reply_text("Tổng dung lượng đã đạt 500 MB.")
        return

    folder = _workdir(context)
    destination = folder / f"{len(files) + 1}_download.zip"
    status_message = await message.reply_text(
        "Đang tải ZIP từ liên kết HTTPS. Quá trình tải được ghi trực tiếp xuống ổ đĩa..."
    )
    try:
        name, actual_size = await download_zip_from_url(
            url, destination, max_bytes=link_limit - current_total
        )
        files.append((name, str(destination)))
        await status_message.edit_text(
            f"Đã tải và kiểm tra ZIP hợp lệ: {name} "
            f"({actual_size / MIB:.1f} MB). Đã nhận {len(files)}/{MAX_FILES} file."
        )
    except DownloadError as exc:
        destination.unlink(missing_ok=True)
        await status_message.edit_text(f"Không tải được ZIP: {exc}")
    except Exception as exc:
        destination.unlink(missing_ok=True)
        await status_message.edit_text(
            f"Tải liên kết thất bại: {type(exc).__name__}. File nguồn không bị thay đổi."
        )


async def inspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return
    folder = _workdir(context)
    report_path = folder / "aki-inspection-report.txt"
    status_message = await update.effective_message.reply_text(
        "Đang phân tích danh mục, đường dẫn trùng, CRC và dấu hiệu tài nguyên. Chế độ chỉ đọc, không sửa ZIP gốc..."
    )
    try:
        report = await asyncio.to_thread(inspect_archives, [path for _, path in files])
        report_path.write_text(report, encoding="utf-8")
        preview = report[:3500]
        await status_message.edit_text(
            preview + ("\n\nBáo cáo đầy đủ được gửi kèm dưới dạng TXT." if len(report) > 3500 else "")
        )
        await update.effective_message.reply_document(
            document=FSInputFile(str(report_path), filename="aki-inspection-report.txt"),
            read_timeout=120,
            write_timeout=120,
        )
    except InspectError as exc:
        await status_message.edit_text(f"Không kiểm tra được: {exc}")
    except Exception as exc:
        await status_message.edit_text(
            f"Kiểm tra thất bại: {type(exc).__name__}. ZIP gốc vẫn được giữ."
        )





async def decode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return

    folder = _workdir(context)
    output_path = folder / "aki-aov-decoded-resources.zip"
    status_message = await update.effective_message.reply_text(
        "Đang giải mã các tài nguyên .bytes AoV có định dạng được hỗ trợ. ZIP nguồn không bị chỉnh sửa..."
    )
    try:
        report = await asyncio.to_thread(
            decode_archive_resources,
            [path for _, path in files],
            str(output_path),
        )
        output_size = output_path.stat().st_size if output_path.exists() else 0
        if not output_size:
            await status_message.edit_text(
                "Không tạo được gói giải mã. Xem báo cáo lỗi hoặc dùng /notice-scan để tìm ứng viên thông báo."
            )
            return
        if not LOCAL_API and output_size > 48 * MIB:
            await status_message.edit_text(
                f"Đã giải mã {len(report.get('decoded', []))} tài nguyên "
                f"({output_size / MIB:.1f} MB), nhưng Bot API mặc định không gửi được file lớn hơn 50 MB. "
                "ZIP kết quả vẫn được giữ trong phiên làm việc."
            )
            return
        await status_message.edit_text(
            f"Đã giải mã {len(report.get('decoded', []))} tài nguyên "
            f"({output_size / MIB:.1f} MB). Đang gửi ZIP..."
        )
        await update.effective_message.reply_document(
            document=FSInputFile(str(output_path), filename="aki-aov-decoded-resources.zip"),
            read_timeout=1800,
            write_timeout=1800,
            connect_timeout=60,
            pool_timeout=60,
        )
    except AOVBytesArchiveError as exc:
        await status_message.edit_text(f"Không giải mã được: {exc}")
    except Exception as exc:
        await status_message.edit_text(
            f"Giải mã thất bại: {type(exc).__name__}. ZIP nguồn vẫn được giữ nguyên."
        )


async def notice_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return

    folder = _workdir(context)
    report_path = folder / "aki-aov-notice-scan.json"
    status_message = await update.effective_message.reply_text(
        "Đang giải mã các .bytes được hỗ trợ và dò chuỗi thông báo trong Lua/XML/JSON/config cùng metadata AssetBundle. Chế độ chỉ đọc..."
    )
    try:
        report = await asyncio.to_thread(
            decode_archive_resources,
            [path for _, path in files],
            "",
            scan_only=True,
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        candidates = report.get("notice_candidates", [])
        startup_candidates = [
            item for item in candidates
            if item.get("startup_removal_eligible") is True
        ]
        protected_kill_candidates = [
            item for item in candidates
            if item.get("resource_role") == "protected_kill_notification"
        ]
        protected_billboard_candidates = [
            item for item in candidates
            if item.get("resource_role") == "protected_billboard"
        ]
        startup_markers = (
            "gameentrycontrol", "lobbyview", "loginnotice",
            "startup", "firstlogin", "first_login", "first-login",
        )
        startup_opaque = [
            item for item in report.get("unsupported", [])
            if any(
                marker in str(item.get("path") or "").replace("\\", "/").casefold()
                for marker in startup_markers
            )
        ]
        custom_lua_resources = report.get("custom_lua_resources", [])
        report["startup_notice_candidates"] = startup_candidates
        report["protected_kill_notification_candidates"] = protected_kill_candidates
        report["protected_billboard_candidates"] = protected_billboard_candidates
        report["startup_flow_unsupported_resources"] = startup_opaque
        lines = [
            f"Đã quét {report.get('notice_scan_files', 0)} mục.",
            f"Tổng kết từ khóa: {len(candidates)} ứng viên.",
            f"AssetBundle đã quét: {report.get('notice_assetbundles_scanned', 0)}; bỏ qua: {report.get('notice_assetbundles_skipped', 0)}.",
            "",
            f"ỨNG VIÊN STARTUP / LOGIN: {len(startup_candidates)}",
        ]
        for item in startup_candidates[:6]:
            lines.append(
                f"• {item.get('source', '')}/{item.get('path', '')} "
                f"[{item.get('keyword_group', 'keyword')}] "
                f"{item.get('matched_text', '')}\n  {item.get('protection_reason', '')}"
            )
        if not startup_candidates:
            lines.append("Không có ứng viên startup/login đủ điều kiện theo quy tắc đường dẫn.")
        lines.extend([
            "",
            f"THÔNG BÁO HẠ GỤC PHẢI GIỮ: {len(protected_kill_candidates)}",
        ])
        for item in protected_kill_candidates[:6]:
            lines.append(
                f"• GIỮ NGUYÊN {item.get('source', '')}/{item.get('path', '')}"
            )
        lines.extend([
            "",
            f"BILLBOARD PHẢI GIỮ: {len(protected_billboard_candidates)}",
        ])
        for item in protected_billboard_candidates[:6]:
            lines.append(
                f"• GIỮ NGUYÊN {item.get('source', '')}/{item.get('path', '')}"
            )
        lines.extend([
            "",
            f"LUA TÙY BIẾN ĐÃ NHẬN DIỆN HEADER (CHƯA DECOMPILE): {len(custom_lua_resources)}",
        ])
        for item in custom_lua_resources[:6]:
            lines.append(
                f"• {item.get('source', '')}/{item.get('resource_path', '')} "
                f"| {item.get('format_family', '')} | {item.get('decode_mode', '')}"
            )
            lines.append(
                f"  SHA-256: {item.get('resource_sha256', '')}"
            )
            lines.append(
                f"  Chuỗi: {' | '.join(item.get('strings', [])[:12])}"
            )
            lines.append(
                f"  {item.get('note', 'Chỉ nhận diện header và chuỗi, chưa thể sửa bytecode.')}"
            )
        lines.extend([
            "",
            f"STARTUP / LOBBY CHƯA GIẢI MÃ: {len(startup_opaque)}",
        ])
        for item in startup_opaque[:6]:
            lines.append(
                f"• CHƯA ĐỌC ĐƯỢC {item.get('source', '')}/{item.get('path', '')}: "
                f"{item.get('reason', 'Chưa có decoder đã xác minh.')}"
            )
        lines.append(
            "\nBáo cáo chỉ đọc: từ khóa chưa chứng minh popup cụ thể đang hiển thị. "
            "Bot không tự xóa hoặc ghi đè tài nguyên."
        )
        await status_message.edit_text("\n".join(lines))
        await update.effective_message.reply_document(
            document=FSInputFile(str(report_path), filename="aki-aov-notice-scan.json"),
            read_timeout=120,
            write_timeout=120,
        )
    except AOVBytesArchiveError as exc:
        await status_message.edit_text(f"Không quét được tài nguyên: {exc}")
    except Exception as exc:
        await status_message.edit_text(
            f"Quét thất bại: {type(exc).__name__}. ZIP nguồn vẫn được giữ nguyên."
        )


async def preflight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return

    folder = _workdir(context)
    report_path = folder / "aki-aov-preflight.json"
    status_message = await update.effective_message.reply_text(
        "Đang phân loại từng ZIP AoV và kiểm tra adapter. Chế độ chỉ đọc, không sửa file gốc..."
    )
    try:
        report = await asyncio.to_thread(
            analyze_aov_input_set, [path for _, path in files]
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        aov_detected = any(
            item.get("looks_like_aov") for item in report.get("profiles", [])
        )
        if not aov_detected:
            summary = "Không phát hiện cấu trúc MOD AoV; bộ file được phân loại là ZIP thông thường."
        elif report.get("ready_to_merge"):
            summary = (
                f"Đã chọn adapter: {report.get('selected_adapter')}. "
                "Bộ này vượt qua bước phân loại; lệnh /merge sẽ tự xác minh lại phiên bản và payload."
            )
        else:
            blockers = report.get("blockers", [])
            summary = "Chưa có adapter ghép đã xác minh cho bộ này."
            if blockers:
                summary += "\n" + "\n".join(f"• {item}" for item in blockers[:5])
        resource_analysis = report.get("resource_analysis", {})
        mses_diagnostics = resource_analysis.get("mses_diagnostics", {})
        mses_tables = mses_diagnostics.get("tables", [])
        mses_pairs = [
            comparison
            for table in mses_tables
            for comparison in table.get("comparisons", [])
        ]
        differing_rows = sum(
            int(comparison.get("differing_common_rows", 0))
            for comparison in mses_pairs
        )
        invalid_mses_sources = sum(
            1
            for table in mses_tables
            for resource in table.get("resources", [])
            if resource.get("parse_ok") and not resource.get("checksum_valid")
        )
        if mses_tables:
            summary += (
                f"\nMSES đã so sánh: {len(mses_tables)} bảng / {len(mses_pairs)} cặp; "
                f"{differing_rows} hàng chung khác payload; "
                f"{invalid_mses_sources} nguồn có checksum không hợp lệ. "
                "Đây là chẩn đoán chỉ đọc."
            )
        await status_message.edit_text(
            summary + "\n\nBáo cáo JSON đầy đủ được gửi kèm."
        )
        await update.effective_message.reply_document(
            document=FSInputFile(str(report_path), filename="aki-aov-preflight.json"),
            read_timeout=120,
            write_timeout=120,
        )
    except ModPipelineError as exc:
        await status_message.edit_text(f"Không phân loại được bộ AoV: {exc}")
    except Exception as exc:
        await status_message.edit_text(
            f"Preflight thất bại: {type(exc).__name__}. ZIP nguồn vẫn được giữ nguyên."
        )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    _cleanup(context)
    await update.effective_message.reply_text("Đã xóa danh sách file.")


async def merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có file nào. Gửi ZIP trước nhé.")
        return

    folder = _workdir(context)
    output_path = folder / "aki-merged.zip"
    status_message = await update.effective_message.reply_text(
        "Đang kiểm tra xung đột, ghép ZIP và đọc lại từng tệp để xác minh SHA-256 + CRC. ZIP gốc được giữ nguyên..."
    )
    try:
        input_paths = [path for _, path in files]
        aov_report = await asyncio.to_thread(analyze_aov_input_set, input_paths)
        aov_detected = any(
            item.get("looks_like_aov") for item in aov_report.get("profiles", [])
        )

        if aov_detected:
            report_path = folder / "aki-aov-preflight.json"
            report_path.write_text(
                json.dumps(aov_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not aov_report.get("ready_to_merge"):
                blockers = aov_report.get("blockers", [])
                detail = "\n".join(f"• {item}" for item in blockers[:4])
                await status_message.edit_text(
                    "Đã nhận diện dữ liệu Liên Quân nhưng chưa có adapter ghép đã xác minh cho bộ này. "
                    "Bot không ghép ZIP thường để tránh mất skin/hiệu ứng/âm thanh.\n"
                    + detail
                    + "\nBáo cáo preflight được gửi kèm; ZIP nguồn vẫn được giữ nguyên."
                )
                await update.effective_message.reply_document(
                    document=FSInputFile(str(report_path), filename="aki-aov-preflight.json"),
                    read_timeout=120,
                    write_timeout=120,
                )
                return

            await status_message.edit_text(
                f"Đã chọn adapter {aov_report.get('selected_adapter')}. "
                "Đang ghép tài nguyên AoV và xác minh cấu trúc, checksum, dependency..."
            )
            merge_result = await asyncio.to_thread(
                merge_mod_archives, input_paths, str(output_path)
            )
            output_size = Path(output_path).stat().st_size
            if not LOCAL_API and output_size > 48 * MIB:
                await status_message.edit_text(
                    f"Đã ghép AoV và kiểm tra cấu trúc xong ({output_size / MIB:.1f} MB), "
                    "nhưng Bot API mặc định không gửi được file lớn hơn 50 MB. "
                    "ZIP nguồn và ZIP kết quả vẫn được giữ trên phiên làm việc."
                )
                return
            await status_message.edit_text(
                f"Đã ghép AoV bằng adapter {merge_result.get('adapter')} "
                f"({output_size / MIB:.1f} MB). Đang gửi file..."
            )
            await update.effective_message.reply_document(
                document=FSInputFile(str(output_path), filename="aki-aov-merged-verified.zip"),
                read_timeout=1800,
                write_timeout=1800,
                connect_timeout=60,
                pool_timeout=60,
            )
            await update.effective_message.reply_document(
                document=FSInputFile(str(report_path), filename="aki-aov-preflight.json"),
                read_timeout=120,
                write_timeout=120,
            )
            _cleanup(context)
            return

        safety_report = await asyncio.to_thread(inspect_archives, input_paths)
        nested_line = next(
            (line for line in safety_report.splitlines()
             if line.startswith("- ZIP/lưu trữ lồng đã mở:")),
            "",
        )
        try:
            nested_count = int(nested_line.rsplit(":", 1)[1].strip()) if nested_line else 0
        except (IndexError, ValueError):
            nested_count = 0

        if nested_count or "MSES v7" in safety_report:
            report_path = folder / "aov-merge-safety-report.txt"
            report_path.write_text(safety_report, encoding="utf-8")
            await status_message.edit_text(
                "Đã phát hiện dữ liệu AoV lồng hoặc bảng MSES nhưng không vượt qua nhận diện MOD chuyên sâu. "
                "Bot dừng ghép ZIP thường để tránh làm hỏng tài nguyên game. "
                "Báo cáo được gửi kèm; ZIP nguồn vẫn được giữ nguyên."
            )
            await update.effective_message.reply_document(
                document=FSInputFile(str(report_path), filename="aov-merge-safety-report.txt"),
                read_timeout=120,
                write_timeout=120,
            )
            return

        output_size = await asyncio.to_thread(
            merge_zip_files,
            input_paths,
            str(output_path),
        )
        if not LOCAL_API and output_size > 48 * MIB:
            await status_message.edit_text(
                f"Đã ghép và xác minh xong ({output_size / MIB:.1f} MB), nhưng Bot API mặc định "
                "chỉ cho bot gửi file tối đa 50 MB. File nguồn và kết quả vẫn được giữ trên phiên làm việc; "
                "cần Local Bot API hoặc cách lưu trữ kết quả bên ngoài để gửi file này."
            )
            return
        await status_message.edit_text(
            f"Đã ghép và xác minh xong ({output_size / MIB:.1f} MB). Đang gửi file..."
        )
        await update.effective_message.reply_document(
            document=FSInputFile(str(output_path), filename="aki-merged-verified.zip"),
            read_timeout=1800,
            write_timeout=1800,
            connect_timeout=60,
            pool_timeout=60,
        )
        _cleanup(context)
    except ModPipelineError as exc:
        await status_message.edit_text(f"Không ghép được MOD AoV: {exc}\\nZIP nguồn vẫn được giữ nguyên.")
    except MergeError as exc:
        await status_message.edit_text(f"Không ghép được: {exc}\nZIP nguồn vẫn được giữ nguyên.")
    except Exception as exc:
        await status_message.edit_text(
            f"Gửi hoặc xử lý thất bại: {type(exc).__name__}. "
            "Các file nguồn vẫn được giữ lại, dùng /merge để thử lại."
        )


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Thiếu biến môi trường BOT_TOKEN")

    builder = Application.builder().token(token)
    api_base = os.environ.get("TELEGRAM_API_BASE_URL")
    file_base = os.environ.get("TELEGRAM_FILE_BASE_URL")
    if api_base and file_base:
        builder = builder.base_url(api_base).base_file_url(file_base)

    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("files", files_list))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("merge", merge))
    app.add_handler(CommandHandler("inspect", inspect))
    app.add_handler(CommandHandler("preflight", preflight))
    app.add_handler(CommandHandler("notice-scan", notice_scan))
    app.add_handler(CommandHandler("decode", decode))
    app.add_handler(MessageHandler(filters.Document.ALL, receive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link))
    app.run_polling()


if __name__ == "__main__":
    main()
