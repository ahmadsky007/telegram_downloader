import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Update
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from .bot import BotState, router
from .config import Settings, load_settings

load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)


def build_bot(settings: Settings) -> Bot:
    session = None
    if settings.telegram_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_url))
    return Bot(token=settings.bot_token, session=session)


def build_dispatcher(settings: Settings) -> Dispatcher:
    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)
    dp = Dispatcher()
    dp.include_router(router)
    dp["st"] = BotState(settings)
    return dp


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    bot = build_bot(settings)
    dp = build_dispatcher(settings)
    app.state.settings = settings
    app.state.bot = bot
    app.state.dp = dp
    if settings.bot_mode == "webhook":
        if not settings.webhook_url:
            raise RuntimeError("WEBHOOK_URL is required in webhook mode")
        await bot.set_webhook(
            f"{settings.webhook_url}/webhook",
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("webhook set to %s/webhook", settings.webhook_url)
    yield
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    settings: Settings = request.app.state.settings
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.webhook_secret:
        raise HTTPException(status_code=403)
    data = await request.json()
    update = Update.model_validate(data, context={"bot": request.app.state.bot})
    await request.app.state.dp.feed_update(request.app.state.bot, update)
    return {"ok": True}


async def run_polling() -> None:
    settings = load_settings()
    bot = build_bot(settings)
    dp = build_dispatcher(settings)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_polling())
