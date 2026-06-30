import json
import logging

from openai import OpenAI

from app.config import settings
from app.models import AnswerQuality, InterviewAnalysisRequest, InterviewAnalysisResult, QuestionAnswerPair
from app.services.qa_extractor import (
    QAExtractionError,
    QAEvaluationError,
    evaluate_question_answer_pairs,
    extract_question_answer_pairs,
    polish_extracted_answer_for_pair,
    regenerate_ideal_answer_for_pair,
)
from app.services.speaker_labeler import SpeakerLabelingError, label_speakers
from app.services.step_inferrer import infer_interview_step
from app.services.result_store import ResultStoreError, result_store

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """You write interview debriefs focused on helping the candidate improve.

Feedback must be SPECIFIC and ACTIONABLE. Every bullet must cite a topic, question, or moment from the interview.

Never write vague advice such as:
- "Provide more specific examples"
- "Communicate more clearly"
- "Elaborate on your experience"

candidate_feedback (5-8 bullets):
- Tell the candidate exactly what to change in future answers
- Reference the question topic (not question number)
- Say what was missing, what to lead with, or a better structure
- Prioritize the lowest-scoring answers and recurring gaps across questions

overall_recommendation:
- One of: hire / no-hire / needs-follow-up
- Include 2-3 concrete reasons tied to scores, gaps, and role fit

Use only evidence from the provided Q&A evaluations. Do not invent questions or answers.
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


def regenerate_ideal_answer(
    recording_url: str,
    question_index: int,
) -> QuestionAnswerPair:
    """Generate or refresh the better answer for one saved Q&A pair without changing its score."""
    lookup = InterviewAnalysisRequest(recording_url=recording_url)
    try:
        payload = result_store.load_payload(lookup)
    except ResultStoreError as exc:
        raise AnalysisError(f"Could not load saved result: {exc}") from exc

    if not payload:
        raise AnalysisError("No saved analysis found for this recording")

    result = InterviewAnalysisResult.model_validate(payload["result"])
    if question_index < 0 or question_index >= len(result.qa_pairs):
        raise AnalysisError(f"Question index {question_index} is out of range")

    saved_request = InterviewAnalysisRequest.model_validate(payload["request"])
    qa = result.qa_pairs[question_index]

    try:
        regenerated = regenerate_ideal_answer_for_pair(
            saved_request,
            question=qa.question,
            answer=qa.answer,
            strengths=qa.strengths,
            gaps=qa.gaps,
        )
    except QAEvaluationError as exc:
        raise AnalysisError(str(exc)) from exc

    updated_qa = qa.model_copy(
        update={
            "ideal_answer": regenerated["ideal_answer"],
            "ideal_answer_points": regenerated["ideal_answer_points"],
            "ideal_answer_generated": True,
            "ideal_answer_source": "regenerated",
        }
    )
    qa_pairs = list(result.qa_pairs)
    qa_pairs[question_index] = updated_qa
    updated_result = result.model_copy(update={"qa_pairs": qa_pairs})
    result_store.save(saved_request, updated_result)

    logger.info("Regenerated ideal answer for question %d on %s", question_index + 1, recording_url)
    return updated_qa


def polish_extracted_answer(
    recording_url: str,
    question_index: int,
) -> QuestionAnswerPair:
    """Proofread the extracted answer and save it as the ideal answer."""
    lookup = InterviewAnalysisRequest(recording_url=recording_url)
    try:
        payload = result_store.load_payload(lookup)
    except ResultStoreError as exc:
        raise AnalysisError(f"Could not load saved result: {exc}") from exc

    if not payload:
        raise AnalysisError("No saved analysis found for this recording")

    result = InterviewAnalysisResult.model_validate(payload["result"])
    if question_index < 0 or question_index >= len(result.qa_pairs):
        raise AnalysisError(f"Question index {question_index} is out of range")

    saved_request = InterviewAnalysisRequest.model_validate(payload["request"])
    qa = result.qa_pairs[question_index]
    if not (qa.answer or "").strip():
        raise AnalysisError("This question has no extracted answer to save")
    if not qa.ideal_answer_generated:
        raise AnalysisError(
            "Generate a better answer first so you can compare, then save your extracted answer if you prefer it."
        )

    try:
        polished = polish_extracted_answer_for_pair(
            saved_request,
            question=qa.question,
            answer=qa.answer,
        )
    except QAEvaluationError as exc:
        raise AnalysisError(str(exc)) from exc

    updated_qa = qa.model_copy(
        update={
            "ideal_answer": polished["ideal_answer"],
            "ideal_answer_points": polished["ideal_answer_points"],
            "ideal_answer_generated": True,
            "ideal_answer_source": "polished_extracted",
        }
    )
    qa_pairs = list(result.qa_pairs)
    qa_pairs[question_index] = updated_qa
    updated_result = result.model_copy(update={"qa_pairs": qa_pairs})
    result_store.save(saved_request, updated_result)

    logger.info("Polished extracted answer for question %d on %s", question_index + 1, recording_url)
    return updated_qa


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
            "ideal_answer_generated": False,
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
            "overall_recommendation": "Not evaluated — question bank mode.",
        },
    }


def _build_summary(request: InterviewAnalysisRequest, evaluated_pairs: list[dict]) -> dict:
    client = OpenAI(api_key=settings.openai_api_key)

    pair_summaries = []
    for index, item in enumerate(evaluated_pairs, start=1):
        pair_summaries.append(
            {
                "number": index,
                "question": item["question"],
                "score": item["score"],
                "quality": item["quality"],
                "strengths": item["strengths"],
                "gaps": item["gaps"],
                "answer_excerpt": item["answer"][:600],
            }
        )

    sorted_pairs = sorted(evaluated_pairs, key=lambda item: item["score"])
    weakest = [
        {
            "question": item["question"][:200],
            "score": item["score"],
            "gaps": item["gaps"],
        }
        for item in sorted_pairs[:3]
    ]
    strongest = [
        {
            "question": item["question"][:200],
            "score": item["score"],
            "strengths": item["strengths"],
        }
        for item in sorted_pairs[-3:]
    ]
    average_score = round(
        sum(item["score"] for item in evaluated_pairs) / len(evaluated_pairs),
        1,
    )

    context_parts = []
    if request.role_title:
        context_parts.append(f"Role title: {request.role_title}")
    if request.role_description:
        context_parts.append(f"Role description: {request.role_description}")
    if request.evaluation_criteria:
        context_parts.append("Evaluation criteria:\n- " + "\n- ".join(request.evaluation_criteria))

    user_prompt = f"""Create an interview debrief from these evaluated Q&A pairs.

{chr(10).join(context_parts) if context_parts else "No extra role context provided."}

Interview stats:
- Total questions: {len(evaluated_pairs)}
- Average score: {average_score}/10

Weakest answers (focus candidate_feedback here):
{json.dumps(weakest, indent=2)}

Strongest answers (reference in highlights and recommendation):
{json.dumps(strongest, indent=2)}

All evaluated Q&A pairs:
{json.dumps(pair_summaries, indent=2)}

Return JSON:
{{
  "transcript_summary": "2-3 sentence overview of performance and role fit",
  "topics_covered": ["topic1", "topic2"],
  "red_flags": ["specific concern tied to an answer, or empty list"],
  "highlights": ["specific strong moment tied to a question topic"],
  "feedback": {{
    "candidate_feedback": [
      "Specific advice citing the question topic and what to say differently"
    ],
    "overall_recommendation": "hire|no-hire|needs-follow-up — 2-3 sentence rationale"
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

