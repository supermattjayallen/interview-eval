import json
import logging
import re
from typing import TypedDict

from openai import OpenAI

from app.config import settings
from app.models import InterviewAnalysisRequest

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You extract substantive interview questions and candidate answers from a speaker-labeled transcript.

Your job is to build a useful question bank — not a verbatim log of every interviewer utterance.

INCLUDE as questions:
- Technical, behavioral, experience, motivation, and role-fit questions
- Follow-ups that assess the candidate ("Can you go deeper on that?", "What was your role there?")
- "Tell me about yourself", "Why this role/company?", system design, coding, leadership, etc.

EXCLUDE — do not extract these as questions:
- Greetings and small talk (weather, weekend, location chit-chat, "how are you", "good thanks")
- Acknowledgments and check-ins ("Does that make sense?", "Okay?", "Right?", "Great", "Perfect")
- Interviewer monologue or company pitch without asking the candidate anything
- Broken transcript fragments or incomplete sentences that are not real questions
- Pure scheduling/process logistics ("Who did you speak with?", "Did you talk to HR?", "Can you hear me?")
- Rhetorical filler where no substantive answer is expected

Rules:
- Extract every substantive interviewer question, including follow-ups and short clarifications
- Treat each distinct substantive question as a separate item
- Do not merge multiple questions into one entry
- For each question, copy the candidate's full answer from the transcript
- Include all candidate sentences until the next interviewer question begins
- Do not shorten, paraphrase lightly, or summarize answers
- Preserve the candidate's wording; answers may be long and multi-paragraph
- If no answer was given, set answer to "" and answer_timestamp to null
- Use speaker labels as ground truth
- When unsure whether an utterance is a real interview question, omit it
- Return valid JSON only"""


EVALUATION_SYSTEM_PROMPT = """You evaluate extracted interview question-and-answer pairs.

For each pair:
1. Score the candidate answer 0-10
2. Identify strengths and gaps

Rules:
- Judge only the provided question and answer
- Do not write a suggested better answer — scoring and gap analysis only
- Be fair but rigorous
- Return valid JSON only"""


IDEAL_ANSWER_SYSTEM_PROMPT = """You write a stronger interview answer a candidate could give.

Rules:
- Write a complete, spoken-style response (2-6 paragraphs when needed)
- Directly address the question and cover important points the candidate missed
- Do not score, critique, or summarize — only produce the improved answer
- Return valid JSON only"""


POLISH_EXTRACTED_SYSTEM_PROMPT = """You lightly edit a candidate's interview answer so it reads naturally when spoken aloud.

Rules:
- Preserve every fact, example, metric, and claim from the original answer
- Do not invent experience, tools, or outcomes the candidate did not mention
- Fix grammar, filler, repetition, and awkward phrasing
- Keep the same structure and level of detail unless trimming obvious verbal clutter
- Write in first person as the candidate
- Return valid JSON only"""


class ExtractedQA(TypedDict):
    question: str
    question_timestamp: str | None
    answer: str
    answer_timestamp: str | None


class QAExtractionError(Exception):
    pass


class QAEvaluationError(Exception):
    pass


def extract_question_answer_pairs(
    request: InterviewAnalysisRequest,
    labeled_transcript: str,
) -> list[ExtractedQA]:
    chunks = _split_transcript(labeled_transcript)
    all_pairs: list[ExtractedQA] = []

    for index, chunk in enumerate(chunks):
        logger.info("Extracting Q&A from transcript chunk %d/%d", index + 1, len(chunks))
        chunk_pairs = _extract_chunk(request, chunk, chunk_index=index, chunk_total=len(chunks))
        all_pairs.extend(chunk_pairs)

    deduped = _dedupe_pairs(all_pairs)
    filtered = _filter_non_questions(deduped)
    if len(filtered) < len(deduped):
        logger.info(
            "Filtered %d non-question pairs (%d kept)",
            len(deduped) - len(filtered),
            len(filtered),
        )
    logger.info("Extracted %d question-and-answer pairs", len(filtered))
    return filtered


def evaluate_question_answer_pairs(
    request: InterviewAnalysisRequest,
    pairs: list[ExtractedQA],
) -> list[dict]:
    if not pairs:
        return []

    evaluated: list[dict] = []
    batch_size = 4

    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        logger.info("Evaluating Q&A batch %d-%d", start + 1, start + len(batch))
        evaluated.extend(_evaluate_batch(request, batch, start_index=start))

    return evaluated


def _extract_chunk(
    request: InterviewAnalysisRequest,
    chunk: str,
    chunk_index: int,
    chunk_total: int,
) -> list[ExtractedQA]:
    client = OpenAI(api_key=settings.openai_api_key)

    user_prompt = f"""Extract substantive interviewer questions and the candidate's full answers from this transcript chunk.

Skip greetings, small talk, acknowledgments ("does that make sense"), scheduling logistics, and incomplete transcript fragments.

Chunk {chunk_index + 1} of {chunk_total}
Interviewer label: {request.interviewer_label}
Candidate label: {request.candidate_label}

--- TRANSCRIPT CHUNK START ---
{chunk}
--- TRANSCRIPT CHUNK END ---

Return JSON:
{{
  "qa_pairs": [
    {{
      "question": "full interviewer question text",
      "question_timestamp": "MM:SS or HH:MM:SS or null",
      "answer": "candidate's complete answer copied from transcript",
      "answer_timestamp": "MM:SS or HH:MM:SS or null"
    }}
  ]
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    content = response.choices[0].message.content
    if not content:
        raise QAExtractionError("Question extraction returned empty response")

    try:
        data = json.loads(content)
        pairs = data["qa_pairs"]
        return [_normalize_pair(item) for item in pairs]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise QAExtractionError(f"Failed to parse extracted Q&A: {exc}") from exc


def _evaluate_batch(
    request: InterviewAnalysisRequest,
    batch: list[ExtractedQA],
    start_index: int,
) -> list[dict]:
    client = OpenAI(api_key=settings.openai_api_key)

    context_parts = []
    if request.role_title:
        context_parts.append(f"Role title: {request.role_title}")
    if request.role_description:
        context_parts.append(f"Role description: {request.role_description}")
    if request.evaluation_criteria:
        context_parts.append("Evaluation criteria:\n- " + "\n- ".join(request.evaluation_criteria))

    pairs_payload = []
    for offset, pair in enumerate(batch):
        pairs_payload.append(
            {
                "index": start_index + offset + 1,
                "question": pair["question"],
                "question_timestamp": pair["question_timestamp"],
                "answer": pair["answer"],
                "answer_timestamp": pair["answer_timestamp"],
            }
        )

    user_prompt = f"""Evaluate these extracted interview Q&A pairs.

{chr(10).join(context_parts) if context_parts else "No extra role context provided."}

Pairs:
{json.dumps(pairs_payload, indent=2)}

Return JSON:
{{
  "evaluations": [
    {{
      "index": 1,
      "quality": "excellent|good|partial|weak|incorrect|not_answered",
      "score": 0,
      "strengths": ["..."],
      "gaps": ["..."]
    }}
  ]
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    content = response.choices[0].message.content
    if not content:
        raise QAEvaluationError("Q&A evaluation returned empty response")

    try:
        data = json.loads(content)
        evaluations = data["evaluations"]
        merged: list[dict] = []
        eval_by_index = {item["index"]: item for item in evaluations}

        for offset, pair in enumerate(batch):
            evaluation = eval_by_index.get(start_index + offset + 1, {})
            merged.append(
                {
                    "question": pair["question"],
                    "question_timestamp": pair["question_timestamp"],
                    "answer": pair["answer"],
                    "answer_timestamp": pair["answer_timestamp"],
                    "quality": evaluation.get("quality", "partial"),
                    "score": evaluation.get("score", 0),
                    "strengths": evaluation.get("strengths", []),
                    "gaps": evaluation.get("gaps", []),
                    "ideal_answer": "",
                    "ideal_answer_points": [],
                    "ideal_answer_generated": False,
                }
            )
        return merged
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise QAEvaluationError(f"Failed to parse Q&A evaluation: {exc}") from exc


def regenerate_ideal_answer_for_pair(
    request: InterviewAnalysisRequest,
    *,
    question: str,
    answer: str,
    strengths: list[str] | None = None,
    gaps: list[str] | None = None,
) -> dict[str, str | list[str]]:
    """Regenerate only the better answer and key points for one Q&A pair."""
    client = OpenAI(api_key=settings.openai_api_key)

    context_parts = []
    if request.role_title:
        context_parts.append(f"Role title: {request.role_title}")
    if request.role_description:
        context_parts.append(f"Role description: {request.role_description}")

    pair_payload = {
        "question": question,
        "answer": answer,
        "strengths": strengths or [],
        "gaps": gaps or [],
    }

    user_prompt = f"""Write a better interview answer for this question-and-answer pair.

{chr(10).join(context_parts) if context_parts else "No extra role context provided."}

Pair:
{json.dumps(pair_payload, indent=2)}

Return JSON:
{{
  "ideal_answer": "A complete better answer the candidate could give, written in full sentences",
  "ideal_answer_points": ["key point 1", "key point 2"]
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": IDEAL_ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    content = response.choices[0].message.content
    if not content:
        raise QAEvaluationError("Ideal answer regeneration returned empty response")

    try:
        data = json.loads(content)
        return {
            "ideal_answer": str(data.get("ideal_answer", "")).strip(),
            "ideal_answer_points": [
                str(point).strip() for point in data.get("ideal_answer_points", []) if str(point).strip()
            ],
        }
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise QAEvaluationError(f"Failed to parse regenerated ideal answer: {exc}") from exc


def polish_extracted_answer_for_pair(
    request: InterviewAnalysisRequest,
    *,
    question: str,
    answer: str,
) -> dict[str, str | list[str]]:
    """Proofread the extracted answer for natural spoken delivery without changing its substance."""
    answer = (answer or "").strip()
    if not answer:
        raise QAEvaluationError("No extracted answer to polish")

    client = OpenAI(api_key=settings.openai_api_key)

    context_parts = []
    if request.role_title:
        context_parts.append(f"Role title: {request.role_title}")
    if request.role_description:
        context_parts.append(f"Role description: {request.role_description}")

    pair_payload = {
        "question": question,
        "answer": answer,
    }

    user_prompt = f"""Polish this extracted interview answer for clarity and natural spoken delivery.

{chr(10).join(context_parts) if context_parts else "No extra role context provided."}

Pair:
{json.dumps(pair_payload, indent=2)}

Return JSON:
{{
  "ideal_answer": "The same answer, proofread and made more natural to say aloud",
  "ideal_answer_points": ["key point 1", "key point 2"]
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": POLISH_EXTRACTED_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = response.choices[0].message.content
    if not content:
        raise QAEvaluationError("Polished answer returned empty response")

    try:
        data = json.loads(content)
        polished = str(data.get("ideal_answer", "")).strip()
        if not polished:
            raise QAEvaluationError("Polished answer was empty")
        return {
            "ideal_answer": polished,
            "ideal_answer_points": [
                str(point).strip() for point in data.get("ideal_answer_points", []) if str(point).strip()
            ],
        }
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise QAEvaluationError(f"Failed to parse polished answer: {exc}") from exc


def _split_transcript(transcript: str, max_chars: int = 28000) -> list[str]:
    lines = transcript.splitlines()
    if not lines:
        return [transcript]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def _normalize_pair(item: dict) -> ExtractedQA:
    return {
        "question": str(item.get("question", "")).strip(),
        "question_timestamp": item.get("question_timestamp"),
        "answer": str(item.get("answer", "")).strip(),
        "answer_timestamp": item.get("answer_timestamp"),
    }


_NON_QUESTION_PHRASES = (
    "does that make sense",
    "does this make sense",
    "make sense",
    "you know what i mean",
    "are you still there",
    "can you hear me",
    "how are you",
    "how are you doing",
    "how's it going",
    "good thanks",
    "good thank you",
    "thanks for joining",
    "thank you for joining",
    "nice to meet you",
    "great to meet you",
    "good to meet you",
    "glad it's friday",
    "have a good weekend",
    "who did you speak with",
    "who have you spoken with",
    "who did you talk to",
    "was it lindsay",
    "was it melissa",
    "any questions for me",
    "do you have any questions for us",
)


def is_substantive_question(question: str) -> bool:
    """Return False for greetings, small talk, and other non-question interviewer utterances."""
    return not _is_non_question(question)


def _filter_non_questions(pairs: list[ExtractedQA]) -> list[ExtractedQA]:
    kept: list[ExtractedQA] = []
    for pair in pairs:
        question = pair["question"].strip()
        if not question:
            continue
        if _is_non_question(question):
            continue
        kept.append(pair)
    return kept


def _is_non_question(question: str) -> bool:
    normalized = _normalize_question_text(question)
    if not normalized:
        return True

    if len(normalized) < 8:
        return True

    for phrase in _NON_QUESTION_PHRASES:
        if normalized == phrase or normalized.startswith(f"{phrase} "):
            return True

    words = normalized.split()
    if len(words) <= 4 and "?" not in question:
        if normalized in {"okay", "ok", "great", "perfect", "awesome", "wonderful", "right", "yeah", "yes", "sure"}:
            return True

    if _looks_like_transcript_fragment(question):
        return True

    if _is_small_talk_question(normalized):
        return True

    return False


def _normalize_question_text(question: str) -> str:
    cleaned = re.sub(r"[^\w\s'?]", " ", question.lower())
    return " ".join(cleaned.split())


def _looks_like_transcript_fragment(question: str) -> bool:
    trimmed = question.strip()
    if not trimmed:
        return True

    words = trimmed.split()
    if len(words) <= 3 and not trimmed.endswith("?"):
        return True

    lower = trimmed.lower()
    fragment_endings = (
        " is",
        " are",
        " was",
        " were",
        " the",
        " a",
        " an",
        " and",
        " or",
        " but",
        " so",
        " you",
        " i",
        " we",
        " they",
        " there",
        " here",
        " yes",
    )
    return any(lower.endswith(ending) for ending in fragment_endings)


def _is_small_talk_question(normalized: str) -> bool:
    small_talk_patterns = (
        r"^how do you like living",
        r"^how is the weather",
        r"^how's the weather",
        r"^where are you (based|located|from)",
        r"^did you have a good weekend",
        r"^how was your weekend",
        r"^how is your (day|week|morning|afternoon)",
    )
    return any(re.search(pattern, normalized) for pattern in small_talk_patterns)


def _dedupe_pairs(pairs: list[ExtractedQA]) -> list[ExtractedQA]:
    deduped: list[ExtractedQA] = []
    seen: set[str] = set()

    for pair in pairs:
        if not pair["question"]:
            continue

        key = f"{pair['question_timestamp']}|{pair['question'].lower()}"
        if key in seen:
            continue

        seen.add(key)
        deduped.append(pair)

    return deduped
