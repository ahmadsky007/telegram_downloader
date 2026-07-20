import asyncio
import html
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import downloader
from .config import Settings

logger = logging.getLogger(__name__)
router = Router()

REQUEST_TTL = 900
UPLOAD_TIMEOUT = 900
MAX_ACTIVE_PER_USER = 3
URL_RE = re.compile(r"https?://\S+")
ALLOWED_HOSTS = ("youtube.com", "youtu.be", "instagram.com")


@dataclass
class PendingRequest:
    url: str
    title: str
    duration: int | None
    uploader: str | None
    heights: list[int]
    sizes: dict[int, int | None]
    direct: dict | None
    user_id: int
    chat_id: int
    link_message_id: int
    created: float = field(default_factory=time.monotonic)


class BotState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pending: dict[str, PendingRequest] = {}
        self.history: dict[int, deque] = {}
        self.active: dict[int, int] = {}
        self.tasks: set[asyncio.Task] = set()
        self.download_slots = asyncio.Semaphore(settings.max_concurrent_downloads)


class ProgressReporter:
    def __init__(self, bot: Bot, chat_id: int, message_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.loop = asyncio.get_running_loop()
        self._last_edit = 0.0
        self._last_text = ""

    def hook(self, d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes")
            if total and done:
                pct = int(done * 100 / total // 5 * 5)
                text = f"⏬ Downloading… {pct}%"
            else:
                text = "⏬ Downloading…"
        elif status in ("finished", "started", "processing"):
            text = "🔄 Converting…"
        else:
            return
        self._schedule(text)

    def _schedule(self, text: str) -> None:
        now = time.monotonic()
        if text == self._last_text:
            return
        if "Downloading" in text and now - self._last_edit < 3:
            return
        self._last_edit = now
        self._last_text = text
        asyncio.run_coroutine_threadsafe(self.set_stage(text), self.loop)

    async def set_stage(self, text: str) -> None:
        try:
            await self.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=self.message_id
            )
        except Exception:
            pass


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    url = match.group(0).rstrip(").,")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in ALLOWED_HOSTS or any(host.endswith("." + h) for h in ALLOWED_HOSTS):
        return url
    return None


def allow_request(st: BotState, user_id: int) -> bool:
    q = st.history.setdefault(user_id, deque())
    now = time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= st.settings.max_requests_per_minute:
        return False
    q.append(now)
    return True


def prune_pending(st: BotState) -> None:
    now = time.monotonic()
    expired = [rid for rid, req in st.pending.items() if now - req.created > REQUEST_TTL]
    for rid in expired:
        st.pending.pop(rid, None)


def size_label(size: int | None) -> str:
    if not size:
        return ""
    return f" · ~{size / 1_000_000:.0f} MB"


def short_error(exc: Exception) -> str:
    text = str(exc).replace("ERROR: ", "").strip()
    return text[:200] if text else "unknown error"


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Send me a YouTube or Instagram link and I'll download it "
        "as a video or MP3.\n\nOnly download content you have the right to save."
    )


@router.message(F.text)
async def handle_link(message: Message, st: BotState) -> None:
    url = extract_url(message.text or "")
    if url is None:
        await message.reply("Please send a valid YouTube or Instagram link.")
        return
    if not allow_request(st, message.from_user.id):
        await message.reply("⏳ Too many requests. Please wait a minute and try again.")
        return
    prune_pending(st)
    status = await message.reply("🔎 Fetching media info…")
    try:
        info = await asyncio.to_thread(downloader.probe, url)
    except Exception as exc:
        logger.warning("probe failed for %s: %s", url, exc)
        await status.edit_text(f"❌ Could not read this link.\n{short_error(exc)}")
        return
    rid = uuid.uuid4().hex[:10]
    st.pending[rid] = PendingRequest(
        url=info["url"],
        title=info["title"],
        duration=info["duration"],
        uploader=info["uploader"],
        heights=info["heights"],
        sizes=info["sizes"],
        direct=info["direct"],
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        link_message_id=message.message_id,
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Video", callback_data=f"v:{rid}"),
                InlineKeyboardButton(text="🎵 MP3", callback_data=f"a:{rid}"),
            ],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data=f"x:{rid}")],
        ]
    )
    await status.edit_text(f"🎞 {info['title']}\n\nDownload as:", reply_markup=keyboard)


async def _get_request(cb: CallbackQuery, st: BotState, rid: str) -> PendingRequest | None:
    req = st.pending.get(rid)
    if req is None:
        await cb.answer("This request expired. Send the link again.", show_alert=True)
        try:
            await cb.message.delete()
        except Exception:
            pass
        return None
    if cb.from_user.id != req.user_id:
        await cb.answer("This request belongs to another user.", show_alert=True)
        return None
    return req


@router.callback_query(F.data.startswith("x:"))
async def cb_cancel(cb: CallbackQuery, st: BotState) -> None:
    rid = cb.data.split(":", 1)[1]
    req = st.pending.get(rid)
    if req is not None and cb.from_user.id != req.user_id:
        await cb.answer("This request belongs to another user.", show_alert=True)
        return
    st.pending.pop(rid, None)
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer("Cancelled")


@router.callback_query(F.data.startswith("v:"))
async def cb_video_menu(cb: CallbackQuery, st: BotState) -> None:
    rid = cb.data.split(":", 1)[1]
    req = await _get_request(cb, st, rid)
    if req is None:
        return
    best_size = req.sizes.get(req.heights[0]) if req.heights else None
    buttons = [
        InlineKeyboardButton(
            text=f"⭐ Best{size_label(best_size)}", callback_data=f"dv:{rid}:0"
        )
    ]
    buttons += [
        InlineKeyboardButton(
            text=f"{h}p{size_label(req.sizes.get(h))}", callback_data=f"dv:{rid}:{h}"
        )
        for h in req.heights
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data=f"x:{rid}")])
    await cb.message.edit_text(
        f"🎞 {req.title}\n\nChoose video quality:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("a:"))
async def cb_audio_menu(cb: CallbackQuery, st: BotState) -> None:
    rid = cb.data.split(":", 1)[1]
    req = await _get_request(cb, st, rid)
    if req is None:
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"{b} kbps{size_label(int(req.duration * b * 125) if req.duration else None)}",
                callback_data=f"da:{rid}:{b}",
            )
            for b in downloader.AUDIO_BITRATES
        ],
        [InlineKeyboardButton(text="✖️ Cancel", callback_data=f"x:{rid}")],
    ]
    await cb.message.edit_text(
        f"🎵 {req.title}\n\nChoose MP3 bitrate:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


async def send_direct_link(
    cb: CallbackQuery, st: BotState, req: PendingRequest, est: int
) -> None:
    title = html.escape(req.title)
    est_mb = est / 1_000_000
    if req.direct is None:
        await cb.message.edit_text(
            f"🎞 {title}\n\n📦 Estimated ~{est_mb:.0f} MB — above my "
            f"{st.settings.max_file_size_mb} MB send limit, and no direct link is "
            "available for this quality. Please pick a lower quality."
        )
        return
    link = req.direct["url"]
    label = f"{req.direct['height']}p {req.direct['ext'].upper()}"
    note = ""
    if req.direct["height"] < 720:
        note = (
            "\n\nℹ️ Higher qualities aren't available as a single direct file on this "
            "platform — the bot can only deliver them itself."
        )
    await cb.message.edit_text(
        f"🎞 {title}\n\n📦 Estimated ~{est_mb:.0f} MB — too big for me to send "
        f"(limit {st.settings.max_file_size_mb} MB), so here's an instant direct "
        f"link instead (expires in a few hours):\n\n"
        f'⬇️ <a href="{link}">Download {label}</a>{note}',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith(("dv:", "da:")))
async def cb_download(cb: CallbackQuery, bot: Bot, st: BotState) -> None:
    kind, rid, value = cb.data.split(":", 2)
    req = await _get_request(cb, st, rid)
    if req is None:
        return
    if st.active.get(cb.from_user.id, 0) >= MAX_ACTIVE_PER_USER:
        await cb.answer(
            f"⏳ You already have {MAX_ACTIVE_PER_USER} downloads running. "
            "Wait for one to finish.",
            show_alert=True,
        )
        return
    limit_bytes = st.settings.max_file_size_mb * 1_000_000
    if kind == "dv":
        height = int(value) or None
        est = req.sizes.get(height) if height else (
            req.sizes.get(req.heights[0]) if req.heights else None
        )
        if est and est > limit_bytes:
            st.pending.pop(rid, None)
            await cb.answer()
            await send_direct_link(cb, st, req, est)
            return
    st.pending.pop(rid, None)
    st.active[req.user_id] = st.active.get(req.user_id, 0) + 1
    await cb.answer("Started — the file will arrive when it's ready")
    task = asyncio.create_task(
        _run_download(cb, bot, st, kind, rid, value, req)
    )
    st.tasks.add(task)
    task.add_done_callback(st.tasks.discard)


async def _run_download(
    cb: CallbackQuery,
    bot: Bot,
    st: BotState,
    kind: str,
    rid: str,
    value: str,
    req: PendingRequest,
) -> None:
    workdir = Path(st.settings.download_dir) / rid
    workdir.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporter(bot, cb.message.chat.id, cb.message.message_id)
    delivered = False
    try:
        await reporter.set_stage("⏳ Queued…")
        async with st.download_slots:
            await reporter.set_stage("⏬ Downloading…")
            if kind == "dv":
                height = int(value) or None
                path = await asyncio.to_thread(
                    downloader.download_video, req.url, workdir, height, reporter.hook
                )
            else:
                path = await asyncio.to_thread(
                    downloader.download_mp3, req.url, workdir, int(value), reporter.hook
                )
        size_mb = path.stat().st_size / 1_000_000
        if size_mb > st.settings.max_file_size_mb:
            raise downloader.DownloadError(
                f"File is {size_mb:.0f} MB, above the {st.settings.max_file_size_mb} MB limit. "
                "Try a lower quality."
            )
        await reporter.set_stage("⬆️ Uploading…")
        duration = int(req.duration) if req.duration else None
        meta = (
            await asyncio.to_thread(downloader.video_meta, path)
            if kind == "dv"
            else {}
        )
        for attempt in range(2):
            try:
                if kind == "dv":
                    await bot.send_video(
                        req.chat_id,
                        FSInputFile(path),
                        reply_to_message_id=req.link_message_id,
                        supports_streaming=True,
                        duration=duration,
                        width=meta.get("width"),
                        height=meta.get("height"),
                        request_timeout=UPLOAD_TIMEOUT,
                    )
                else:
                    await bot.send_audio(
                        req.chat_id,
                        FSInputFile(path),
                        reply_to_message_id=req.link_message_id,
                        title=req.title,
                        performer=req.uploader,
                        duration=duration,
                        request_timeout=UPLOAD_TIMEOUT,
                    )
                break
            except TelegramNetworkError:
                if attempt:
                    raise
                logger.warning("upload failed for %s, retrying", req.url)
                await asyncio.sleep(3)
        delivered = True
    except Exception as exc:
        logger.exception("download failed for %s", req.url)
        try:
            await bot.edit_message_text(
                f"❌ Failed: {short_error(exc)}",
                chat_id=cb.message.chat.id,
                message_id=cb.message.message_id,
            )
        except Exception:
            pass
    finally:
        remaining = st.active.get(req.user_id, 1) - 1
        if remaining > 0:
            st.active[req.user_id] = remaining
        else:
            st.active.pop(req.user_id, None)
        downloader.cleanup(workdir)
        if delivered:
            try:
                await bot.delete_message(cb.message.chat.id, cb.message.message_id)
            except Exception:
                pass
