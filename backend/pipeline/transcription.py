from faster_whisper import WhisperModel

from .schemas import TranscriptResult, WordTimestamp, TranscriptSegment


_model_instance: WhisperModel | None = None


def get_model(model_size: str = "base") -> WhisperModel:
    global _model_instance
    if _model_instance is None:
        _model_instance = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_instance


def transcribe_audio(
    audio_path: str,
    model_size: str = "base",
    language: str | None = None,
) -> TranscriptResult:
    model = get_model(model_size)

    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
    )

    all_words: list[WordTimestamp] = []
    all_segments: list[TranscriptSegment] = []
    full_text_parts: list[str] = []

    for segment in segments:
        segment_words: list[WordTimestamp] = []
        for word in segment.words:
            wt = WordTimestamp(
                word=word.word.strip(),
                start=word.start,
                end=word.end,
                probability=word.probability,
            )
            segment_words.append(wt)
            all_words.append(wt)
            full_text_parts.append(word.word.strip())

        all_segments.append(
            TranscriptSegment(
                start=segment.start,
                end=segment.end,
                text=segment.text.strip(),
                words=segment_words,
            )
        )

    return TranscriptResult(
        language=info.language,
        language_probability=info.language_probability,
        duration=info.duration,
        text=" ".join(full_text_parts),
        segments=all_segments,
        words=all_words,
    )
