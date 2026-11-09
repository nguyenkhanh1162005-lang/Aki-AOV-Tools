import os, asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from app.merger import merge_zips, MergeError

MAX_BYTES = 20 * 1024 * 1024
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["zips"] = []
    await update.message.reply_text("Gửi lần lượt các file ZIP, sau đó dùng /merge để ghép. Dùng /clear để xóa danh sách.")

async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("Hãy gửi file .zip nhé.")
        return
    if doc.file_size and doc.file_size > MAX_BYTES:
        await update.message.reply_text("File vượt quá giới hạn 20 MB của bot.")
        return
    files = context.user_data.setdefault("zips", [])
    if len(files) >= 10:
        await update.message.reply_text("Đã đủ 10 file. Dùng /merge hoặc /clear.")
        return
    tg_file = await doc.get_file()
    data = await tg_file.download_as_bytearray()
    files.append((doc.file_name, bytes(data)))
    await update.message.reply_text(f"Đã nhận {len(files)}/10 file: {doc.file_name}")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["zips"] = []
    await update.message.reply_text("Đã xóa danh sách file.")

async def merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = context.user_data.get("zips", [])
    if not files:
        await update.message.reply_text("Chưa có file nào. Gửi ZIP trước nhé.")
        return
    try:
        result = merge_zips([data for _, data in files])
    except MergeError as exc:
        await update.message.reply_text(str(exc))
        return
    from io import BytesIO
    await update.message.reply_document(document=BytesIO(result), filename="aki-merged.zip")
    context.user_data["zips"] = []

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Thiếu biến môi trường BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("merge", merge))
    app.add_handler(MessageHandler(filters.Document.ALL, receive))
    app.run_polling()

if __name__ == "__main__":
    main()
