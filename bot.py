import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram import FSInputFile

from app.large_merger import merge_zip_files, MergeError
from app.aov_inspector import inspect_archives, InspectError
from app.url_downloader import download_zip_from_url, DownloadError

MIB = 1024 * 1024
LOCAL_API = bool(
    os.environ.get("TELEGRAM_API_BASE_URL")
    and os.environ.get("TELEGRAM_FILE_BASE_URL")
)
MAX_FILE_BYTES = 500 * MIB if LOCAL_API else 20 * MIB
MAX_TOTAL_BYTES = 500 * MIB if LOCAL_API else 100 * MIB
MAX_FILES = 10


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
    _cleanup(context)
    mode = "500 MB/file, tổng 500 MB" if LOCAL_API else "20 MB/file (Bot API mặc định)"
    await update.message.reply_text(
        "Aki-AOV Bot đã sẵn sàng.\n"
        f"Chế độ nhận file: {mode}.\n\n"
        "Gửi ZIP nhỏ trực tiếp hoặc gửi liên kết HTTPS tải ZIP trực tiếp (tối đa 500 MB), sau đó dùng /merge để ghép.\n"
        "/status xem giới hạn hiện tại\n"
        "/inspect kiểm tra cấu trúc ZIP đã gửi\n"
        "/clear xóa danh sách file"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = context.user_data.get("zips", [])
    total = sum(Path(path).stat().st_size for _, path in files if Path(path).exists())
    mode = "Local Bot API" if LOCAL_API else "Bot API mặc định"
    await update.message.reply_text(
        f"Chế độ: {mode}\n"
        f"Giới hạn mỗi ZIP: {MAX_FILE_BYTES // MIB} MB\n"
        f"Tổng dung lượng nhận: {MAX_TOTAL_BYTES // MIB} MB\n"
        f"Đã nhận: {len(files)}/{MAX_FILES} file, {total / MIB:.1f} MB"
    )


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    doc = message.document if message else None
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.reply_text("Hãy gửi file .zip dưới dạng Tệp nhé.")
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
    message = update.effective_message
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
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có ZIP nào. Gửi file trước nhé.")
        return
    status_message = await update.effective_message.reply_text(
        "Đang đọc danh mục ZIP, không sửa hoặc giải mã tài nguyên..."
    )
    try:
        report = await asyncio.to_thread(inspect_archives, [path for _, path in files])
        chunks = [report[i:i + 3800] for i in range(0, len(report), 3800)]
        await status_message.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await update.effective_message.reply_text(chunk)
    except InspectError as exc:
        await status_message.edit_text(f"Không kiểm tra được: {exc}")
    except Exception as exc:
        await status_message.edit_text(
            f"Kiểm tra thất bại: {type(exc).__name__}. ZIP gốc vẫn được giữ."
        )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup(context)
    await update.effective_message.reply_text("Đã xóa danh sách file.")


async def merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = context.user_data.get("zips", [])
    if not files:
        await update.effective_message.reply_text("Chưa có file nào. Gửi ZIP trước nhé.")
        return

    folder = _workdir(context)
    output_path = folder / "aki-merged.zip"
    status_message = await update.effective_message.reply_text(
        "Đang kiểm tra và ghép ZIP. Đừng gửi /clear trong lúc xử lý nhé."
    )
    try:
        await asyncio.to_thread(
            merge_zip_files,
            [path for _, path in files],
            str(output_path),
        )
        await status_message.edit_text(
            f"Đã ghép xong ({output_path.stat().st_size / MIB:.1f} MB). Đang gửi file..."
        )
        await update.effective_message.reply_document(
            document=FSInputFile(str(output_path), filename="aki-merged.zip"),
            read_timeout=1800,
            write_timeout=1800,
            connect_timeout=60,
            pool_timeout=60,
        )
        _cleanup(context)
    except MergeError as exc:
        await status_message.edit_text(f"Không ghép được: {exc}")
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
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("merge", merge))
    app.add_handler(CommandHandler("inspect", inspect))
    app.add_handler(MessageHandler(filters.Document.ALL, receive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link))
    app.run_polling()


if __name__ == "__main__":
    main()
