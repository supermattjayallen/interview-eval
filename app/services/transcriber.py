import logging
from pathlib import Path

from openai import OpenAI

from app.config import settings
from app.services.audio_processor import AudioProcessingError, prepare_audio_files

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    pass


def transcribe_audio(audio_path: Path, language: str = "en") -> str:
    if not settings.openai_api_key:
        raise TranscriptionError("OPENAI_API_KEY is not configured")

    try:
        audio_files = prepare_audio_files(audio_path)
    except AudioProcessingError as exc:
        raise TranscriptionError(str(exc)) from exc

    transcript_parts: list[str] = []
    time_offset = 0.0

    for index, chunk_path in enumerate(audio_files):
        chunk_transcript, chunk_duration = _transcribe_file(chunk_path, language)
        if len(audio_files) > 1:
            chunk_transcript = _shift_timestamps(chunk_transcript, time_offset)
            time_offset += chunk_duration
        transcript_parts.append(chunk_transcript)

    transcript = "\n".join(part for part in transcript_parts if part).strip()
    if not transcript:
        raise TranscriptionError("Transcription returned empty text")

    logger.info("Transcribed %d characters from %s", len(transcript), audio_path.name)
    return transcript


def _transcribe_file(audio_path: Path, language: str) -> tuple[str, float]:
    client = OpenAI(api_key=settings.openai_api_key)

    try:
        with audio_path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=settings.whisper_model,
                file=audio_file,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except Exception as exc:
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    segments = getattr(response, "segments", None) or []
    duration = float(getattr(response, "duration", 0) or 0)

    if segments:
        lines = []
        for segment in segments:
            start = _format_timestamp(segment.start)
            end = _format_timestamp(segment.end)
            text = segment.text.strip()
            if text:
                lines.append(f"[{start} - {end}] {text}")
        return "\n".join(lines), duration

    return response.text.strip(), duration


def _shift_timestamps(transcript: str, offset_seconds: float) -> str:
    shifted_lines = []
    for line in transcript.splitlines():
        if not line.startswith("[") or " - " not in line:
            shifted_lines.append(line)
            continue

        prefix, text = line.split("] ", 1)
        start_raw, end_raw = prefix[1:].split(" - ")
        start = _parse_timestamp(start_raw) + offset_seconds
        end = _parse_timestamp(end_raw) + offset_seconds
        shifted_lines.append(
            f"[{_format_timestamp(start)} - {_format_timestamp(end)}] {text}"
        )
    return "\n".join(shifted_lines)


def _parse_timestamp(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
