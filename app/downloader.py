import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import yt_dlp

STANDARD_HEIGHTS = (1080, 720, 480, 360)
MAX_HEIGHT = 1080
AUDIO_BITRATES = (320, 192, 128)


class DownloadError(Exception):
    pass


def _base_opts(workdir: Path | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
    }
    if workdir is not None:
        opts["outtmpl"] = str(workdir / "%(title).80B [%(id)s].%(ext)s")
    return opts


def probe(url: str) -> dict:
    with yt_dlp.YoutubeDL(_base_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("No downloadable media found at this link.")
        info = entries[0]
    formats = info.get("formats", [])
    heights = {
        f["height"]
        for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }
    available = [h for h in STANDARD_HEIGHTS if heights and h <= max(heights)]
    audio_sizes = [
        f.get("filesize") or f.get("filesize_approx") or 0
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    audio_size = max(audio_sizes, default=0)
    sizes: dict[int, int | None] = {}
    for h in available:
        video_height = max((fh for fh in heights if fh <= h), default=None)
        candidates = [
            f.get("filesize") or f.get("filesize_approx") or 0
            for f in formats
            if f.get("height") == video_height and f.get("vcodec") not in (None, "none")
        ]
        total = max(candidates, default=0) + audio_size
        sizes[h] = total or None
    combined = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
        and f.get("url")
        and f.get("protocol") in ("http", "https")
        and f.get("height")
    ]
    direct = None
    if combined:
        best = max(combined, key=lambda f: f["height"])
        direct = {
            "url": best["url"],
            "height": best["height"],
            "ext": best.get("ext") or "mp4",
        }
    return {
        "title": info.get("title") or "media",
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "url": info.get("webpage_url") or url,
        "heights": available,
        "sizes": sizes,
        "direct": direct,
    }


def download_video(url: str, workdir: Path, height: int | None, hook: Callable) -> Path:
    height = min(height or MAX_HEIGHT, MAX_HEIGHT)
    fmt = (
        f"bv*[vcodec~='^(avc1|h264)'][height<={height}]+ba[ext=m4a]/"
        f"bv*[vcodec~='^(avc1|h264)'][height<={height}]+ba/"
        f"b[height<={height}]/"
        f"b/"
        f"bv*[height<={height}]+ba"
    )
    opts = _base_opts(workdir) | {
        "format": fmt,
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "postprocessor_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return _find_output(workdir, (".mp4", ".mkv", ".webm", ".mov"))


def download_mp3(url: str, workdir: Path, bitrate: int, hook: Callable) -> Path:
    opts = _base_opts(workdir) | {
        "format": "ba/b",
        "progress_hooks": [hook],
        "postprocessor_hooks": [hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            },
            {"key": "FFmpegMetadata"},
        ],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return _find_output(workdir, (".mp3",))


def video_meta(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "v:0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        stream = json.loads(result.stdout)["streams"][0]
        return {"width": stream.get("width"), "height": stream.get("height")}
    except (KeyError, IndexError, json.JSONDecodeError):
        return {}


def _find_output(workdir: Path, exts: tuple[str, ...]) -> Path:
    files = [p for p in workdir.iterdir() if p.suffix.lower() in exts]
    if not files:
        raise DownloadError("Processing finished but no output file was produced.")
    return max(files, key=lambda p: p.stat().st_size)


def cleanup(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
