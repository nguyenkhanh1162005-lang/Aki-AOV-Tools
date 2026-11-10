# Deploy Aki AOV Tools with large Telegram ZIP support

This Compose stack runs the web app, the Telegram worker, and a self-hosted Telegram Bot API server on one private Docker network. The bot talks to the local API over the internal service name; port 8081 is not published to the public internet.

## Requirements

- A Linux host with Docker Engine and the Docker Compose plugin.
- Enough free disk space for the source ZIPs, extracted temporary data, output ZIP, and persistent Telegram API data. For the current 150 MB candidate, reserve several GB rather than allocating only the archive size.
- Telegram `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org), plus the bot token from BotFather.

The `aiogram/telegram-bot-api` image is a community Docker image for Telegram's open-source Bot API server. The server itself is maintained by Telegram's TDLib project. Keep the image tag under review and update it deliberately.

## Configure

1. Copy `.env.example` to `.env`.
2. Fill in `BOT_TOKEN`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH`.
3. Keep `.env` private. It is ignored by Git and must never be committed or pasted into chat.
4. Start the stack:

```sh
docker compose -f docker-compose.local-bot-api.yml up -d --build
```

The web app listens on port 8000. Put it behind an HTTPS reverse proxy before exposing it publicly. The Local Bot API server is only exposed to the Compose network.

## Move the bot from Telegram's hosted API

Do this only when ready to move the bot. Do not run two polling workers for the same bot.

1. Stop the existing Telegram bot worker on Render or any other host. Leave the web service alone if it is separate.
2. Call Telegram's `logOut` method once on the hosted Bot API, before starting the local worker:

```sh
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/logOut"
```

3. Start the Compose stack. The bot container uses:
   - `TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot`
   - `TELEGRAM_FILE_BASE_URL=http://telegram-bot-api:8081/file/bot`
4. Check logs:

```sh
docker compose -f docker-compose.local-bot-api.yml logs -f telegram-bot-api bot
```

Telegram requires `logOut` before switching to a local Bot API server. Switching back to Telegram's hosted API also requires logging out from the local server first.

## File limits in this project

When both local API URLs are configured, `bot.py` enables the project's current limits:

- Up to 500 MiB per input ZIP.
- Up to 500 MiB total input across up to 10 ZIPs.
- Output ZIP up to 1 GiB in the shared ZIP merger.
- Telegram's Local Bot API itself supports downloads without the hosted 20 MB download cap and uploads up to 2000 MB.

The project limit is intentionally lower than Telegram's maximum. A large ZIP merge also needs RAM and temporary disk space. This configuration does not make the bot/game merge engine support arbitrary AoV versions; it remains locked to the documented 1.64.1.12 base and 1.64.1.11 Vấn Tâm Kiếm pack.

## Operational notes

- `docker compose down` stops services but keeps the named Telegram data volume.
- `docker compose down -v` also deletes that persistent data volume; avoid it unless you intend to reset the local Bot API server.
- Never publish port 8081 directly to the internet. The bot worker reaches it over the private Compose network.
- This deployment guide does not claim that the bot is deployed or that the merged ZIP has passed an iOS in-game test.
