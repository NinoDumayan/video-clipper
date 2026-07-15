import json
import os
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from .schemas import TranscriptResult, HighlightClip


def _build_prompt(transcript: TranscriptResult) -> str:
    return f"""You are a viral clip analyst. Analyze this video transcript and identify the best moments that would make engaging short-form clips for platforms like TikTok, YouTube Shorts, or Instagram Reels.

TRANSCRIPT:
{transcript.text}

DURATION: {transcript.duration:.1f} seconds

RULES:
- Return ONLY raw JSON — no markdown fences, no code blocks, no explanation.
- Each clip MUST be between 15 and 90 seconds long.
- Prioritize moments with high emotional impact, surprising revelations, or strong opinions.
- Assign a virality_score from 0-100 based on: hook strength, emotional impact, shareability, and completeness.
- Escape any double quotes or special characters inside string values.
- Do not include trailing commas.

Return a JSON object with this exact structure:
{{
  "clips": [
    {{
      "title": "Short catchy title for the clip",
      "start_time": <start_time_in_seconds>,
      "end_time": <end_time_in_seconds>,
      "virality_score": <0-100>,
      "reason": "Why this clip would perform well"
    }}
  ]
}}

Return between 1 and 5 clips. Prioritize quality over quantity."""


def _parse_clips_response(response_text: str) -> list[dict[str, Any]]:
    text = response_text.strip()

    text = re.sub(r"(?s)^.*?(\{)", r"\1", text)
    text = re.sub(r"(?s)(\})[^}]*$", r"\1", text)

    text = re.sub(r"```(?:json)?", "", text).strip()

    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*\]", "]", text)

    text = re.sub(r"//[^\n]*", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import ast
            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            brace_start = text.find("{")
            brace_end = text.rfind("}")
            if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
                inner = text[brace_start:brace_end + 1]
                inner = re.sub(r",\s*}", "}", inner)
                data = json.loads(inner)
            else:
                raise

    clips_data: list[dict[str, Any]] = data.get("clips", data if isinstance(data, list) else [])
    return clips_data


async def extract_highlights_gemini(
    transcript: TranscriptResult,
    api_key: str | None = None,
) -> list[HighlightClip]:
    api_key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        msg = "GEMINI_API_KEY is not set. Provide it via .env.local or the api_key parameter."
        raise ValueError(msg)

    client = genai.Client(api_key=api_key)

    prompt = _build_prompt(transcript)

    response = await client.aio.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=8192,
        ),
    )

    raw = response.text or ""
    if not raw.strip():
        raise ValueError("Gemini returned an empty response")

    clips_data = _parse_clips_response(raw)

    clips: list[HighlightClip] = []
    for i, c in enumerate(clips_data):
        clips.append(
            HighlightClip(
                index=i,
                title=c.get("title", f"Clip {i + 1}"),
                start_time=float(c.get("start_time", 0)),
                end_time=float(c.get("end_time", 0)),
                virality_score=int(c.get("virality_score", 50)),
                reason=c.get("reason", ""),
            )
        )

    return clips


async def extract_highlights_ollama(
    transcript: TranscriptResult,
    base_url: str = "http://localhost:11434",
    model: str = "deepseek-r1:7b",
) -> list[HighlightClip]:
    import httpx

    prompt = _build_prompt(transcript)

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        response = await client.post(
            "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        data = response.json()
        response_text = data.get("response", "")

    clips_data = _parse_clips_response(response_text)

    clips: list[HighlightClip] = []
    for i, c in enumerate(clips_data):
        clips.append(
            HighlightClip(
                index=i,
                title=c.get("title", f"Clip {i + 1}"),
                start_time=float(c.get("start_time", 0)),
                end_time=float(c.get("end_time", 0)),
                virality_score=int(c.get("virality_score", 50)),
                reason=c.get("reason", ""),
            )
        )

    return clips


async def extract_highlights(
    transcript: TranscriptResult,
    use_ollama: bool = False,
    gemini_api_key: str | None = None,
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
) -> list[HighlightClip]:
    if use_ollama:
        return await extract_highlights_ollama(
            transcript,
            base_url=ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=ollama_model or os.getenv("OLLAMA_MODEL", "deepseek-r1:7b"),
        )
    else:
        return await extract_highlights_gemini(
            transcript,
            api_key=gemini_api_key,
        )
