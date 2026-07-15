import re
import time
from pathlib import Path

import yt_dlp


YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/"
)

MAX_RETRIES = 3


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_PATTERN.match(url.strip()))


def get_video_title(url: str) -> str:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url.strip(), download=False)
        title: str = info.get("title", "Untitled Video")
        return title


def download_youtube_video(url: str, output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    template = str(output_path.with_suffix(""))

    opts = {
        "format": "best[height<=1080]",
        "outtmpl": f"{template}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "extractor_retries": 3,
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url.strip()])

            final_path = output_path.with_suffix(".mp4")
            if not final_path.exists():
                candidates = list(output_path.parent.glob(f"{output_path.stem}.*"))
                if candidates:
                    final_path = candidates[0]
                else:
                    raise FileNotFoundError(f"Downloaded video not found: {final_path}")

            return str(final_path.resolve())

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = attempt * 2
                time.sleep(wait)

    raise last_error or RuntimeError("Download failed after retries")
