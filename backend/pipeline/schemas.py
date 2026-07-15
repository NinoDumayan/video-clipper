from pydantic import BaseModel
from typing import Literal, Any


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    probability: float


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[WordTimestamp]


class TranscriptResult(BaseModel):
    language: str
    language_probability: float
    duration: float
    text: str
    segments: list[TranscriptSegment]
    words: list[WordTimestamp]


class HighlightClip(BaseModel):
    index: int
    title: str
    start_time: float
    end_time: float
    virality_score: int
    reason: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["uploading", "transcribing", "analyzing", "rendering", "complete", "error"]
    progress: float
    message: str
    clips: list[HighlightClip] = []
    error: str | None = None
    original_filename: str = ""
    duration: float = 0.0


class UploadResponse(BaseModel):
    job_id: str
    status: str
    message: str


class RenderRequest(BaseModel):
    job_id: str
    clip_index: int
    include_subtitles: bool = True
    aspect_ratio: str = "original"
    auto_reframe: bool = False


class RenderResponse(BaseModel):
    render_id: str
    status: str
    message: str


class RenderStatus(BaseModel):
    render_id: str
    status: Literal["queued", "rendering", "complete", "error", "cancelled"]
    message: str
    download_url: str = ""
    error: str | None = None


class CancelResponse(BaseModel):
    status: str
    message: str


class YoutubeRequest(BaseModel):
    url: str
