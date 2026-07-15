import subprocess
from pathlib import Path

from .schemas import TranscriptResult


SHARPEN = "unsharp=7:7:1.5:7:7:0.0"
DEBLOCK = "deblock=strong:8"

ASPECT_RATIOS: dict[str, str] = {
    "original": f"hqdn3d=2:1:3:2,scale=1920:1920:flags=lanczos:force_original_aspect_ratio=decrease,{SHARPEN}",
    "9:16": f"hqdn3d=3:2:5:3,crop=ih*9/16:ih,scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,{DEBLOCK},{SHARPEN}",
    "16:9": f"hqdn3d=2:1:3:2,scale=1920:1080:flags=lanczos:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,{SHARPEN}",
    "1:1": f"hqdn3d=2:1:3:2,crop=ih:ih,scale=1080:1080:flags=lanczos:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2,{SHARPEN}",
    "4:5": f"hqdn3d=3:2:5:3,crop=ih*4/5:ih,scale=864:1080:flags=lanczos:force_original_aspect_ratio=decrease,pad=864:1080:(ow-iw)/2:(oh-ih)/2,{DEBLOCK},{SHARPEN}",
}


def render_clip_ffmpeg(
    video_path: str | Path,
    start_time: float,
    end_time: float,
    output_path: str | Path,
    subtitle_path: str | Path | None = None,
    aspect_ratio: str = "original",
) -> str:
    video_path = Path(video_path)
    output_path = Path(output_path)
    if subtitle_path is not None:
        subtitle_path = Path(subtitle_path)
    duration = end_time - start_time

    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_parts: list[str] = []

    vf = ASPECT_RATIOS.get(aspect_ratio)
    if vf:
        filter_parts.append(vf)

    if subtitle_path and subtitle_path.exists():
        escaped_srt = str(subtitle_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
        filter_parts.append(f"subtitles={escaped_srt}")

    cmd = [
        "ffmpeg",
        "-ss", str(start_time),
        "-i", str(video_path.resolve()),
        "-t", str(duration),
    ]

    if filter_parts:
        cmd.extend(["-vf", ",".join(filter_parts)])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-y",
        str(output_path.resolve()),
    ])

    subprocess.run(cmd, check=True, capture_output=True, text=True)

    if not output_path.exists():
        raise FileNotFoundError(f"Clip rendering failed: {output_path} not found")

    return str(output_path.resolve())


def generate_subtitles(
    transcript: TranscriptResult,
    start_time: float,
    end_time: float,
    output_path: str | Path,
) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    relevant_words = [w for w in transcript.words if start_time <= w.start <= end_time]

    lines: list[str] = []
    subtitle_index = 1
    chunk_size = 5

    for i in range(0, len(relevant_words), chunk_size):
        chunk = relevant_words[i : i + chunk_size]
        chunk_start = chunk[0].start
        chunk_end = chunk[-1].end

        text = " ".join(w.word for w in chunk)

        lines.append(str(subtitle_index))
        lines.append(
            f"{_format_time(chunk_start - start_time)} --> {_format_time(chunk_end - start_time)}"
        )
        lines.append(text)
        lines.append("")
        subtitle_index += 1

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path.resolve())


def _format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")
