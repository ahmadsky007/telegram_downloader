import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    telegram_api_url: str | None
    download_dir: str
    bot_mode: str
    webhook_url: str | None
    webhook_secret: str
    max_concurrent_downloads: int
    max_requests_per_minute: int
    max_file_size_mb: int


def load_settings() -> Settings:
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        telegram_api_url=os.environ.get("TELEGRAM_API_URL") or None,
        download_dir=os.environ.get("DOWNLOAD_DIR", "/tmp/media-bot"),
        bot_mode=os.environ.get("BOT_MODE", "polling"),
        webhook_url=os.environ.get("WEBHOOK_URL") or None,
        webhook_secret=os.environ.get("WEBHOOK_SECRET", "change-me"),
        max_concurrent_downloads=int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3")),
        max_requests_per_minute=int(os.environ.get("MAX_REQUESTS_PER_MINUTE", "5")),
        max_file_size_mb=int(os.environ.get("MAX_FILE_SIZE_MB", "1900")),
    )
