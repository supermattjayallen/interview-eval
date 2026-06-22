import json
import logging

from openai import OpenAI

from app.config import settings
from app.models import InterviewAnalysisRequest

logger = logging.getLogger(__name__)

LABELING_SYSTEM_PROMPT = """You label interview transcript lines with speakers.

Rules:
- Prefix every line with exactly one label: [Interviewer] or [Candidate]
- Keep the original timestamp and text unchanged after the label
- The interviewer asks questions, guides the conversation, and gives brief acknowledgements
- The candidate answers questions and explains their experience in more detail
- Short backchannels like "okay", "right", "mm-hmm" should follow the previous speaker unless clearly a new turn
- Do not merge lines, skip lines, rewrite text, or invent content
- Return valid JSON only"""


class SpeakerLabelingError(Exception):
    pass


def label_speakers(request: InterviewAnalysisRequest, transcript: str) -> str:
    if not settings.openai_api_key:
        raise SpeakerLabelingError("OPENAI_API_KEY is not configured")

    lines = [line for line in transcript.splitlines() if line.strip()]
    if not lines:
        raise SpeakerLabelingError("Transcript is empty")

    labeled_chunks: list[str] = []
    chunk_size = 120
    overlap = 8

    for start in range(0, len(lines), chunk_size - overlap):
        chunk_lines = lines[start : start + chunk_size]
        if not chunk_lines:
            continue

        context = labeled_chunks[-1].splitlines()[-overlap:] if labeled_chunks else []
        labeled_chunk = _label_chunk(request, chunk_lines, context)
        if labeled_chunks:
            labeled_chunk = _drop_overlap(labeled_chunks[-1], labeled_chunk, overlap)
        labeled_chunks.append(labeled_chunk)

    labeled_transcript = "\n".join(labeled_chunks).strip()
    if not labeled_transcript:
        raise SpeakerLabelingError("Speaker labeling returned empty text")

    logger.info("Labeled transcript with %d lines", labeled_transcript.count("\n") + 1)
    return labeled_transcript


def _label_chunk(
    request: InterviewAnalysisRequest,
    chunk_lines: list[str],
    context_lines: list[str],
) -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    chunk_text = "\n".join(chunk_lines)
    context_text = "\n".join(context_lines)

    user_prompt = f"""Label each transcript line with a speaker.

Interviewer label: {request.interviewer_label}
Candidate label: {request.candidate_label}
First speaker in the interview: {request.first_speaker}

Previous labeled lines for continuity:
{context_text or "(none)"}

Unlabeled lines to label:
{chunk_text}

Return JSON:
{{
  "labeled_lines": [
    "[{request.interviewer_label}] [00:12 - 00:18] example text"
  ]
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": LABELING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    content = response.choices[0].message.content
    if not content:
        raise SpeakerLabelingError("Speaker labeling returned empty response")

    try:
        data = json.loads(content)
        labeled_lines = data["labeled_lines"]
        if not isinstance(labeled_lines, list) or not labeled_lines:
            raise ValueError("labeled_lines missing")
        return "\n".join(str(line) for line in labeled_lines)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise SpeakerLabelingError(f"Failed to parse speaker labels: {exc}") from exc


def _drop_overlap(previous_chunk: str, next_chunk: str, overlap: int) -> str:
    if overlap <= 0:
        return next_chunk

    previous_lines = previous_chunk.splitlines()
    next_lines = next_chunk.splitlines()
    if len(next_lines) <= overlap:
        return next_chunk

    previous_tail = previous_lines[-overlap:]
    for index in range(min(overlap, len(next_lines))):
        if previous_tail[index].strip() == next_lines[index].strip():
            continue
        return "\n".join(next_lines[index:])

    return "\n".join(next_lines[overlap:])
