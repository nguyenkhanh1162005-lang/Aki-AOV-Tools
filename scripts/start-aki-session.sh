#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="docker-compose.local-bot-api.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker chưa được cài trên máy chủ." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "Thiếu file .env. Tạo từ .env.example và điền BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH." >&2
  exit 1
fi

echo "Đang bật Local Bot API và Aki-AOV Bot..."
docker compose -f "$COMPOSE_FILE" up -d --build telegram-bot-api bot
echo
docker compose -f "$COMPOSE_FILE" ps
echo
echo "Bot đã được khởi động. Gửi ZIP trong Telegram, sau đó dùng /inspect và /aovmerge hoặc /merge."
echo "Khi nhận ZIP xong, xử lý xong và đã nhận kết quả, chạy: sh scripts/stop-aki-session.sh"
