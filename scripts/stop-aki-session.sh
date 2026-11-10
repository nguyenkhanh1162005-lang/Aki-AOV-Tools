#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="docker-compose.local-bot-api.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker chưa được cài trên máy chủ." >&2
  exit 1
fi

echo "Đang dừng bot và Local Bot API..."
docker compose -f "$COMPOSE_FILE" down --remove-orphans
echo "Đã dừng phiên Aki-AOV. Dữ liệu volume Local Bot API được giữ lại (không dùng -v)."
