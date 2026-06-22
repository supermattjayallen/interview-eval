import json
import logging

from openai import OpenAI

from app.config import settings
from app.models import AnswerQuality, InterviewAnalysisRequest, InterviewAnalysisResult
from app.services.qa_extractor import (
    QAExtractionError,
    QAEvaluationError,
    evaluate_question_answer_pairs,
    extract_question_answer_pairs,
)
from app.services.speaker_labeler import SpeakerLabelingError, label_speakers
from app.services.step_inferrer import infer_interview_step

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """You summarize interview results based on already-extracted question-and-answer pairs.

Do not invent new questions or answers.
Return valid JSON only."""


class AnalysisError(Exception):
    pass


def analyze_interview(request: InterviewAnalysisRequest, transcript: str) -> InterviewAnalysisResult:
    if not settings.openai_api_key:
        raise AnalysisError("OPENAI_API_KEY is not configured")

    try:
        labeled_transcript = label_speakers(request, transcript)
    except SpeakerLabelingError as exc:
        raise AnalysisError(str(exc)) from exc

    try:
        extracted_pairs = extract_question_answer_pairs(request, labeled_transcript)
        if not extracted_pairs:
            raise QAExtractionError("No questions were extracted from the transcript")

        if request.skip_evaluation:
            qa_pairs = _unevaluated_pairs(extracted_pairs)
            summary = _skipped_evaluation_summary(len(qa_pairs))
            average_score = 0.0
            evaluation_skipped = True
        else:
            evaluated_pairs = evaluate_question_answer_pairs(request, extracted_pairs)
            summary = _build_summary(request, evaluated_pairs)
            qa_pairs = evaluated_pairs
            average_score = round(
                sum(item["score"] for item in evaluated_pairs) / len(evaluated_pairs),
                1,
            )
            evaluation_skipped = False
    except (QAExtractionError, QAEvaluationError) as exc:
        raise AnalysisError(str(exc)) from exc

    interview_step = request.interview_step
    interview_step_inferred = False
    if interview_step is None:
        interview_step = infer_interview_step(
            [item["question"] for item in qa_pairs],
            role_title=request.role_title,
        )
        interview_step_inferred = True

    result = InterviewAnalysisResult(
        recording_url=str(request.recording_url),
        role_title=request.role_title,
        interview_step=interview_step,
        interview_step_inferred=interview_step_inferred,
        transcript_summary=summary["transcript_summary"],
        labeled_transcript_excerpt=_excerpt(labeled_transcript),
        total_questions=len(qa_pairs),
        average_score=average_score,
        qa_pairs=qa_pairs,
        feedback=summary["feedback"],
        topics_covered=summary["topics_covered"],
        red_flags=summary["red_flags"],
        highlights=summary["highlights"],
        evaluation_skipped=evaluation_skipped,
    )

    logger.info(
        "Analysis complete: %d questions%s",
        result.total_questions,
        " (evaluation skipped)" if evaluation_skipped else f", avg score {result.average_score}",
    )
    return result


def reevaluate_interview(
    request: InterviewAnalysisRequest,
    cached_result: InterviewAnalysisResult,
) -> InterviewAnalysisResult:
    """Re-score and refresh feedback using existing Q&A with updated evaluation criteria."""
    if not settings.openai_api_key:
        raise AnalysisError("OPENAI_API_KEY is not configured")

    extracted_pairs = [
        {
            "question": qa.question,
            "question_timestamp": qa.question_timestamp,
            "answer": qa.answer,
            "answer_timestamp": qa.answer_timestamp,
        }
        for qa in cached_result.qa_pairs
    ]

    if not extracted_pairs:
        raise AnalysisError("Saved result has no questions to re-evaluate")

    try:
        evaluated_pairs = evaluate_question_answer_pairs(request, extracted_pairs)
    except QAEvaluationError as exc:
        raise AnalysisError(str(exc)) from exc

    summary = _build_summary(request, evaluated_pairs)
    average_score = round(
        sum(item["score"] for item in evaluated_pairs) / len(evaluated_pairs),
        1,
    )

    interview_step = request.interview_step or cached_result.interview_step
    interview_step_inferred = cached_result.interview_step_inferred and request.interview_step is None

    result = InterviewAnalysisResult(
        recording_url=str(request.recording_url),
        role_title=request.role_title or cached_result.role_title,
        interview_step=interview_step,
        interview_step_inferred=interview_step_inferred,
        transcript_summary=summary["transcript_summary"],
        labeled_transcript_excerpt=cached_result.labeled_transcript_excerpt,
        total_questions=len(evaluated_pairs),
        average_score=average_score,
        qa_pairs=evaluated_pairs,
        feedback=summary["feedback"],
        topics_covered=summary["topics_covered"],
        red_flags=summary["red_flags"],
        highlights=summary["highlights"],
        reevaluated_with_new_context=True,
        evaluation_skipped=False,
    )

    logger.info(
        "Re-evaluated %d questions with updated context, avg score %.1f",
        result.total_questions,
        result.average_score,
    )
    return result


def _unevaluated_pairs(extracted_pairs: list[dict]) -> list[dict]:
    return [
        {
            "question": pair["question"],
            "question_timestamp": pair["question_timestamp"],
            "answer": pair["answer"],
            "answer_timestamp": pair["answer_timestamp"],
            "quality": AnswerQuality.NOT_ANSWERED.value,
            "score": 0,
            "strengths": [],
            "gaps": [],
            "ideal_answer": "",
            "ideal_answer_points": [],
        }
        for pair in extracted_pairs
    ]


def _skipped_evaluation_summary(total_questions: int) -> dict:
    return {
        "transcript_summary": (
            f"Extracted {total_questions} questions and answers. "
            "Answer evaluation was skipped to reduce processing cost."
        ),
        "topics_covered": [],
        "red_flags": [],
        "highlights": [],
        "feedback": {
            "candidate_feedback": [],
            "interviewer_feedback": [],
            "overall_recommendation": "Not evaluated — question bank mode.",
        },
    }


def _build_summary(request: InterviewAnalysisRequest, evaluated_pairs: list[dict]) -> dict:
    client = OpenAI(api_key=settings.openai_api_key)

    compact_pairs = [
        {
            "question": item["question"],
            "answer_excerpt": item["answer"][:500],
            "score": item["score"],
            "quality": item["quality"],
        }
        for item in evaluated_pairs
    ]

    context_parts = []
    if request.role_title:
        context_parts.append(f"Role title: {request.role_title}")
    if request.role_description:
        context_parts.append(f"Role description: {request.role_description}")

    user_prompt = f"""Create a high-level interview summary from these extracted Q&A pairs.

{chr(10).join(context_parts) if context_parts else ""}

Q&A pairs:
{json.dumps(compact_pairs, indent=2)}

Return JSON:
{{
  "transcript_summary": "2-3 sentence overview",
  "topics_covered": ["topic1", "topic2"],
  "red_flags": ["concern if any"],
  "highlights": ["strong moment if any"],
  "feedback": {{
    "candidate_feedback": ["actionable advice"],
    "interviewer_feedback": ["actionable advice"],
    "overall_recommendation": "hire/no-hire/needs-follow-up with rationale"
  }}
}}"""

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    content = response.choices[0].message.content
    if not content:
        raise AnalysisError("Summary generation returned empty response")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"Failed to parse interview summary: {exc}") from exc


def _excerpt(labeled_transcript: str, max_chars: int = 4000) -> str:
    if len(labeled_transcript) <= max_chars:
        return labeled_transcript
    return labeled_transcript[:max_chars] + "\n... [transcript truncated for display]"
