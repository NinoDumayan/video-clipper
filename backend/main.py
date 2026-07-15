import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env.local")
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .pipeline.schemas import (
    UploadResponse,
    JobStatus,
    HighlightClip,
    RenderRequest,
    RenderResponse,
    RenderStatus,
    CancelResponse,
    YoutubeRequest,
)
from .pipeline.audio_extractor import extract_audio, get_video_duration
from .pipeline.transcription import transcribe_audio
from .pipeline.highlight_extractor import extract_highlights
from .pipeline.clip_renderer import render_clip_ffmpeg, generate_subtitles
from .pipeline.reframe import auto_reframe
from .pipeline.youtube_downloader import is_youtube_url, download_youtube_video, get_video_title

app = FastAPI(title="Clipper AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.url.path == "/api/login":
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] not in sessions:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
USE_OLLAMA = bool(os.getenv("OLLAMA_BASE_URL")) and not os.getenv("GEMINI_API_KEY")

APP_PASSWORD = os.getenv("APP_PASSWORD", "admin")
sessions: dict[str, str] = {}

jobs_store: dict[str, JobStatus] = {}
render_jobs_store: dict[str, RenderStatus] = {}
cancelled_renders: set[str] = set()
cancelled_jobs: set[str] = set()


def require_auth(token: str | None) -> None:
    if not token or token not in sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/login")
async def login(data: dict):
    password = data.get("password", "")
    if password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = str(uuid.uuid4())
    sessions[token] = "admin"
    return {"token": token}


async def _run_pipeline(job_id: str, video_path: Path) -> None:
    job = jobs_store.get(job_id)
    if not job:
        return

    if job_id in cancelled_jobs:
        job.status = "error"
        job.message = "Cancelled"
        return

    try:
        job.duration = await asyncio.to_thread(get_video_duration, str(video_path))
    except Exception:
        pass

    if job_id in cancelled_jobs:
        job.status = "error"
        job.message = "Cancelled"
        return

    job.status = "transcribing"
    job.progress = 0.2
    job.message = "Transcribing audio..."

    audio_path = UPLOAD_DIR / f"{job_id}.wav"
    try:
        await asyncio.to_thread(extract_audio, str(video_path), str(audio_path))
    except Exception as e:
        job.status = "error"
        job.message = f"Audio extraction failed: {e}"
        return

    if job_id in cancelled_jobs:
        job.status = "error"
        job.message = "Cancelled"
        return

    try:
        transcript = await asyncio.to_thread(
            transcribe_audio, str(audio_path), WHISPER_MODEL_SIZE,
        )
    except Exception as e:
        job.status = "error"
        job.message = f"Transcription failed: {e}"
        return

    if job_id in cancelled_jobs:
        job.status = "error"
        job.message = "Cancelled"
        return

    job.status = "analyzing"
    job.progress = 0.6
    job.message = f"Transcription complete ({transcript.language}). Analyzing for viral clips..."

    try:
        clips = await extract_highlights(
            transcript,
            use_ollama=USE_OLLAMA,
        )
    except Exception as e:
        job.status = "error"
        job.message = f"Highlight analysis failed: {e}"
        return

    if job_id in cancelled_jobs:
        job.status = "error"
        job.message = "Cancelled"
        return

    sorted_clips = sorted(clips, key=lambda c: c.virality_score, reverse=True)
    for i, c in enumerate(sorted_clips):
        c.index = i

    job.status = "complete"
    job.progress = 1.0
    job.message = f"Found {len(sorted_clips)} viral clips!"
    job.clips = sorted_clips
    job.duration = transcript.duration


async def _background_process_youtube(job_id: str, url: str) -> None:
    job = jobs_store.get(job_id)
    if not job:
        return

    job.message = "Fetching video info..."
    try:
        title = await asyncio.to_thread(get_video_title, url)
    except Exception as e:
        job.status = "error"
        job.message = f"Failed to fetch video info: {e}"
        return

    job.original_filename = title
    job.message = f"Downloading: {title}..."

    video_path = UPLOAD_DIR / f"{job_id}.mp4"

    try:
        await asyncio.to_thread(download_youtube_video, url, str(video_path))
    except Exception as e:
        job.status = "error"
        job.message = f"YouTube download failed: {e}"
        return

    await _run_pipeline(job_id, video_path)


async def _background_process_upload(job_id: str, video_path: Path) -> None:
    await _run_pipeline(job_id, video_path)


@app.post("/api/upload-video", response_model=UploadResponse)
async def upload_video(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Only .mp4 files are supported")

    job_id = str(uuid.uuid4())
    video_path = UPLOAD_DIR / f"{job_id}.mp4"

    content = await file.read()
    video_path.write_bytes(content)

    jobs_store[job_id] = JobStatus(
        job_id=job_id,
        status="uploading",
        progress=0.05,
        message="Upload complete. Starting transcription...",
        original_filename=file.filename,
    )

    asyncio.create_task(_background_process_upload(job_id, video_path))

    return UploadResponse(job_id=job_id, status="processing", message="Video uploaded and pipeline started")


@app.post("/api/process-youtube", response_model=UploadResponse)
async def process_youtube(req: YoutubeRequest):
    url = req.url.strip()
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    job_id = str(uuid.uuid4())

    jobs_store[job_id] = JobStatus(
        job_id=job_id,
        status="uploading",
        progress=0.0,
        message="Queued...",
        original_filename="YouTube Video",
    )

    asyncio.create_task(_background_process_youtube(job_id, url))

    return UploadResponse(job_id=job_id, status="processing", message="YouTube video queued for processing")


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    job = jobs_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


async def _background_render(render_id: str, job_id: str, clip_idx: int, include_subtitles: bool, aspect_ratio: str, auto_reframe_enabled: bool = False) -> None:
    rs = render_jobs_store.get(render_id)
    if not rs:
        return

    job = jobs_store.get(job_id)
    if not job or clip_idx >= len(job.clips):
        rs.status = "error"
        rs.message = "Invalid clip"
        return

    clip = job.clips[clip_idx]
    video_path = UPLOAD_DIR / f"{job_id}.mp4"
    if not video_path.exists():
        rs.status = "error"
        rs.message = "Original video not found"
        return

    rs.status = "rendering"
    rs.message = f"Rendering: {clip.title}..."

    output_filename = f"{job_id}_clip_{clip_idx}.mp4"
    output_path = UPLOAD_DIR / output_filename

    def _is_cancelled() -> bool:
        return render_id in cancelled_renders

    if _is_cancelled():
        rs.status = "cancelled"
        rs.message = "Cancelled"
        return

    srt_path = None
    if include_subtitles:
        try:
            if _is_cancelled():
                rs.status = "cancelled"
                rs.message = "Cancelled"
                return
            audio_path = UPLOAD_DIR / f"{job_id}.wav"
            if audio_path.exists():
                transcript = await asyncio.to_thread(transcribe_audio, str(audio_path), WHISPER_MODEL_SIZE)
                if transcript:
                    srt_path = UPLOAD_DIR / f"{job_id}_clip_{clip_idx}.srt"
                    await asyncio.to_thread(generate_subtitles, transcript, clip.start_time, clip.end_time, str(srt_path))
        except Exception:
            pass

    if _is_cancelled():
        rs.status = "cancelled"
        rs.message = "Cancelled"
        return

    try:
        if auto_reframe_enabled and aspect_ratio in ("9:16", "4:5", "1:1"):
            await asyncio.to_thread(
                auto_reframe,
                str(video_path), clip.start_time, clip.end_time,
                str(output_path),
                1080, 1920 if aspect_ratio == "9:16" else 1080,
                str(srt_path) if srt_path and srt_path.exists() else None,
            )
        else:
            await asyncio.to_thread(
                render_clip_ffmpeg,
                str(video_path), clip.start_time, clip.end_time,
                str(output_path),
                str(srt_path) if srt_path and srt_path.exists() else None,
                aspect_ratio,
            )
    except Exception as e:
        if _is_cancelled():
            rs.status = "cancelled"
            rs.message = "Cancelled"
        else:
            rs.status = "error"
            rs.message = f"Rendering failed: {e}"
            rs.error = str(e)
        return

    if _is_cancelled():
        rs.status = "cancelled"
        rs.message = "Cancelled"
        if output_path.exists():
            output_path.unlink()
        return

    rs.status = "complete"
    rs.message = f"Rendered: {clip.title}"
    rs.download_url = f"/api/download/{output_filename}"


@app.post("/api/render-clip", response_model=RenderResponse)
async def render_clip(req: RenderRequest):
    job = jobs_store.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {req.job_id} not found")
    if req.clip_index < 0 or req.clip_index >= len(job.clips):
        raise HTTPException(status_code=400, detail=f"Invalid clip index {req.clip_index}")

    render_id = str(uuid.uuid4())
    render_jobs_store[render_id] = RenderStatus(
        render_id=render_id,
        status="queued",
        message="Queued for rendering...",
    )

    asyncio.create_task(_background_render(render_id, req.job_id, req.clip_index, req.include_subtitles, req.aspect_ratio, req.auto_reframe))

    return RenderResponse(render_id=render_id, status="queued", message="Render queued")


@app.get("/api/render-status/{render_id}", response_model=RenderStatus)
async def get_render_status(render_id: str):
    rs = render_jobs_store.get(render_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Render job not found")
    return rs


@app.post("/api/render-cancel/{render_id}", response_model=CancelResponse)
async def cancel_render(render_id: str):
    rs = render_jobs_store.get(render_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Render job not found")
    if rs.status in ("complete", "cancelled"):
        return CancelResponse(status=rs.status, message=f"Render already {rs.status}")
    cancelled_renders.add(render_id)
    rs.status = "cancelled"
    rs.message = "Cancelling..."
    return CancelResponse(status="cancelling", message="Cancelling render")


@app.post("/api/jobs/{job_id}/cancel", response_model=CancelResponse)
async def cancel_job(job_id: str):
    job = jobs_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled_jobs.add(job_id)
    job.status = "error"
    job.message = "Cancelled"
    return CancelResponse(status="cancelled", message="Job cancelled")


@app.get("/api/download/{filename}")
async def download_clip(filename: str):
    safe_path = (UPLOAD_DIR / filename).resolve()
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(safe_path), media_type="video/mp4", filename=filename)
