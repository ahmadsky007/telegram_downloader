# Telegram Media Downloader Bot

Send the bot a YouTube, Instagram, X/Twitter, TikTok, Reddit, Facebook or Pinterest link → pick **Video** (with quality menu) or **MP3** (with bitrate menu) → receive the file. The bot cleans up after itself: temp files are deleted and all intermediate menus/progress messages are removed, leaving only your link and the delivered media.

Built with Python, [aiogram 3](https://docs.aiogram.dev/), FastAPI, [yt-dlp](https://github.com/yt-dlp/yt-dlp), and FFmpeg.

> Only download content you own or have the right to save. Respect each platform's terms of service and copyright law.

## Architecture

```
User → Telegram → (local Bot API server, optional) → bot (aiogram)
                                                      ├─ yt-dlp probe → format/bitrate menus
                                                      ├─ yt-dlp + FFmpeg download/convert (thread pool, semaphore-limited)
                                                      └─ upload file → delete temp dir + status message
```

- **Polling mode** (`BOT_MODE=polling`) — for local dev: `python -m app.main`
- **Webhook mode** (`BOT_MODE=webhook`) — FastAPI app for Cloud Run: `uvicorn app.main:app`
- **Local Bot API server** — raises the upload limit from 50 MB to 2 GB. Without it (empty `TELEGRAM_API_URL`) the bot uses the official API and can only send files under 50 MB.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. `cp .env.example .env` and fill in `BOT_TOKEN`.
3. Local run (official API, 50 MB limit):

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m app.main
   ```

   FFmpeg must be installed (`brew install ffmpeg`).

4. Full run with 2 GB uploads (Docker):
   - Get `api_id`/`api_hash` from [my.telegram.org](https://my.telegram.org) → "API development tools", put them in `.env` as `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`.
   - If the bot has ever talked to the official Bot API, call `https://api.telegram.org/bot<TOKEN>/logOut` once before switching to the local server (Telegram requires this; the bot is locked out of the cloud API for 10 minutes after).
   - `docker compose up --build`

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | — | Telegram bot token (required) |
| `BOT_MODE` | `polling` | `polling` or `webhook` |
| `TELEGRAM_API_URL` | official API | Local Bot API server URL, e.g. `http://localhost:8081` |
| `WEBHOOK_URL` | — | Public base URL (webhook mode) |
| `WEBHOOK_SECRET` | `change-me` | Secret token Telegram echoes back on webhook calls |
| `DOWNLOAD_DIR` | `/tmp/media-bot` | Temp workspace, one subdir per request, always cleaned |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Global download semaphore |
| `MAX_REQUESTS_PER_MINUTE` | `5` | Per-user rate limit |
| `MAX_FILE_SIZE_MB` | `1900` | Refuse uploads above this (set 49 when on the official API) |

## Deploying to Cloud Run (later)

- Deploy this image with `BOT_MODE=webhook`, `WEBHOOK_URL=https://<service-url>`, and a random `WEBHOOK_SECRET`; the app registers the webhook on startup.
- Run the `aiogram/telegram-bot-api` image as a second Cloud Run service (or sidecar) and point `TELEGRAM_API_URL` at it.
- Store `BOT_TOKEN` in Secret Manager, not plain env vars.
- Note: YouTube often blocks datacenter IPs ("Sign in to confirm you're not a bot"). If that appears in logs, export browser cookies and mount them via yt-dlp's `cookiefile` option.

## Known limits

- Official Bot API caps bot uploads at 50 MB; the local Bot API server raises this to 2 GB.
- One active download per user; menus expire after 15 minutes.
- Playlists are not expanded — only the first/linked item is downloaded.
