import subprocess
from pathlib import Path


def extract_audio(
    video_path: str | Path,
    output_path: str | Path | None = None,
    sample_rate: int = 16000,
) -> str:
    video_path = Path(video_path)
    if output_path is None:
        output_path = video_path.with_suffix(".wav")

    output_path = Path(output_path)

    cmd = [
        "ffmpeg",
        "-i", str(video_path.resolve()),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-y",
        str(output_path.resolve()),
    ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)

    if not output_path.exists():
        raise FileNotFoundError(f"Audio extraction failed: {output_path} not found")

    return str(output_path.resolve())


def get_video_duration(video_path: str | Path) -> float:
    video_path = Path(video_path)
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(video_path.resolve()),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())
