import json
import subprocess as sp
import tempfile
from pathlib import Path

import cv2
import numpy as np


def _probe_stream(video_path: str, key: str) -> str | int | float | None:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", video_path,
    ]
    out = sp.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            return s.get(key)
    return None


def _get_video_fps(video_path: str) -> float:
    r = _probe_stream(video_path, "r_frame_rate")
    if r and isinstance(r, str) and "/" in r:
        num, den = r.split("/")
        return float(num) / float(den)
    return 30.0


def _get_video_resolution(video_path: str) -> tuple[int, int]:
    w = _probe_stream(video_path, "width")
    h = _probe_stream(video_path, "height")
    return (w or 1920, h or 1080)  # type: ignore


def _sample_frames_gray(
    video_path: str, start: float, duration: float,
    sample_fps: float, max_size: int = 320,
) -> tuple[list[np.ndarray], int, int]:
    in_w, in_h = _get_video_resolution(video_path)
    aspect = in_w / in_h
    if aspect >= 1:
        out_w, out_h = max_size, max(1, int(max_size / aspect))
    else:
        out_w, out_h = max(1, int(max_size * aspect)), max_size

    cmd = [
        "ffmpeg", "-ss", str(start), "-i", video_path,
        "-t", str(duration),
        "-s", f"{out_w}x{out_h}",
        "-f", "rawvideo", "-pix_fmt", "gray",
        "-r", str(sample_fps),
        "pipe:1",
    ]
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, bufsize=10**8)

    frame_bytes = out_w * out_h
    frames: list[np.ndarray] = []
    while True:
        buf = proc.stdout.read(frame_bytes)
        if not buf or len(buf) < frame_bytes:
            break
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w))

    proc.stdout.close()
    proc.wait()
    return frames, out_w, out_h


def _compute_trajectory(
    frames: list[np.ndarray],
    in_w: int, in_h: int,
    sample_w: int, sample_h: int,
    out_w: int, out_h: int,
) -> list[tuple[int, int]]:
    if len(frames) < 2:
        cx = in_w // 2
        cy = in_h // 2
        return [(cx, cy)] * max(len(frames), 1)

    centroids: list[tuple[float, float]] = []
    for i in range(1, len(frames)):
        diff = cv2.absdiff(frames[i - 1], frames[i])
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        M = cv2.moments(thresh)
        if M["m00"] > 50:
            centroids.append((M["m10"] / M["m00"], M["m01"] / M["m00"]))
        else:
            centroids.append((sample_w / 2.0, sample_h / 2.0))

    cx_arr = np.array([c[0] for c in centroids], dtype=np.float64)
    cy_arr = np.array([c[1] for c in centroids], dtype=np.float64)

    sigma = min(len(cx_arr) // 4, 10)
    if sigma >= 2 and len(cx_arr) > sigma * 3:
        from scipy.ndimage import gaussian_filter1d
        cx_smooth = gaussian_filter1d(cx_arr, sigma=sigma)
        cy_smooth = gaussian_filter1d(cy_arr, sigma=sigma)
    else:
        cx_smooth = cx_arr
        cy_smooth = cy_arr

    scale_x = in_w / sample_w
    scale_y = in_h / sample_h

    ar = out_w / out_h
    crop_w = min(in_h * ar, in_w)
    crop_h = min(in_w / ar, in_h)
    half_w = crop_w / 2.0
    half_h = crop_h / 2.0

    positions: list[tuple[int, int]] = []
    for i in range(len(cx_smooth)):
        cx = cx_smooth[i] * scale_x
        cy = cy_smooth[i] * scale_y
        cx = max(half_w, min(in_w - half_w, cx))
        cy = max(half_h, min(in_h - half_h, cy))
        positions.append((int(cx - half_w), int(cy - half_h)))
    return positions


def auto_reframe(
    video_path: str,
    start_time: float,
    end_time: float,
    output_path: str,
    target_width: int = 1080,
    target_height: int = 1920,
    subtitle_path: str | None = None,
) -> str:
    duration = end_time - start_time
    fps = _get_video_fps(video_path)
    in_w, in_h = _get_video_resolution(video_path)

    sample_fps = max(10, min(fps, 15))
    frames, sw, sh = _sample_frames_gray(video_path, start_time, duration, sample_fps)

    trajectory = _compute_trajectory(frames, in_w, in_h, sw, sh, target_width, target_height)

    total_frames = int(duration * fps)
    crop_positions: list[tuple[int, int]] = []
    if len(trajectory) > 1:
        for i in range(total_frames):
            t = i / fps
            idx_f = t * sample_fps
            idx = int(idx_f)
            frac = idx_f - idx
            if idx >= len(trajectory) - 1:
                crop_positions.append(trajectory[-1])
            else:
                x0, y0 = trajectory[idx]
                x1, y1 = trajectory[idx + 1]
                crop_positions.append((int(x0 + (x1 - x0) * frac), int(y0 + (y1 - y0) * frac)))
    elif trajectory:
        crop_positions = [trajectory[0]] * total_frames
    else:
        cx = (in_w - int(min(in_h * target_width / target_height, in_w))) // 2
        cy = (in_h - int(min(in_w * target_height / target_width, in_h))) // 2
        crop_positions = [(cx, cy)] * total_frames

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    ar = target_width / target_height
    crop_w = int(min(in_h * ar, in_w))
    crop_h = int(min(in_w / ar, in_h))

    audio_path = output_path.replace(".mp4", "_audio_temp.m4a")
    _extract_audio(video_path, start_time, duration, audio_path)

    read_cmd = [
        "ffmpeg", "-ss", str(start_time), "-i", video_path,
        "-t", str(duration),
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{in_w}x{in_h}",
        "pipe:1",
    ]
    write_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{crop_w}x{crop_h}",
        "-r", str(fps),
        "-i", "pipe:0",
    ]
    vf = (
        f"hqdn3d=3:2:5:3,"
        f"scale={target_width}:{target_height}:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
        f"deblock=strong:8,unsharp=7:7:1.5:7:7:0.0"
    )
    if subtitle_path and Path(subtitle_path).exists():
        esc = str(Path(subtitle_path).resolve()).replace("\\", "\\\\").replace(":", "\\:")
        vf += f",subtitles={esc}"
        write_cmd.extend(["-vf", vf])
    else:
        write_cmd.extend(["-vf", vf])
    write_cmd.extend([
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        output_path,
    ])

    read_proc = sp.Popen(read_cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, bufsize=10**8)
    write_proc = sp.Popen(write_cmd, stdin=sp.PIPE, stdout=sp.DEVNULL, stderr=sp.PIPE, bufsize=10**8)

    frame_size = in_w * in_h * 3
    frame_idx = 0

    while True:
        raw = read_proc.stdout.read(frame_size)
        if not raw or len(raw) < frame_size:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(in_h, in_w, 3)

        if frame_idx < len(crop_positions):
            x, y = crop_positions[frame_idx]
        else:
            x = (in_w - crop_w) // 2
            y = (in_h - crop_h) // 2

        x = min(max(x, 0), in_w - crop_w)
        y = min(max(y, 0), in_h - crop_h)

        cropped = frame[y:y + crop_h, x:x + crop_w]
        write_proc.stdin.write(cropped.tobytes())
        frame_idx += 1

    read_proc.stdout.close()
    read_proc.wait()
    write_proc.stdin.close()
    write_proc.wait()

    if Path(audio_path).exists():
        _mux_audio(output_path, audio_path)

    if not Path(output_path).exists():
        raise FileNotFoundError(f"Auto-reframe rendering failed: {output_path} not found")

    return str(Path(output_path).resolve())


def _extract_audio(video_path: str, start: float, duration: float, out_path: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", video_path,
        "-t", str(duration),
        "-vn", "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    sp.run(cmd, capture_output=True, check=True)


def _mux_audio(video_path: str, audio_path: str) -> None:
    tmp = video_path.replace(".mp4", "_tmp.mp4")
    Path(video_path).rename(tmp)
    cmd = [
        "ffmpeg", "-y",
        "-i", tmp,
        "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        video_path,
    ]
    sp.run(cmd, capture_output=True, check=True)
    Path(tmp).unlink()
    Path(audio_path).unlink()
