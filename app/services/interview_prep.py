import json
import logging

from openai import OpenAI

from app.config import settings
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep
from app.models import InterviewPrepRequest, InterviewPrepResult, PredictedQuestion
from app.prep_categories import PREP_CATEGORY_LABELS, PrepQuestionCategory
from app.services.job_store import job_store
from app.services.prep_retrieval import (
    build_prep_bank_selection,
    dedupe_predicted_questions,
    restrict_to_bank_questions,
)
from app.services.result_store import result_store

logger = logging.getLogger(__name__)

MAX_BANK_QUESTIONS_IN_PROMPT = 120

PREP_SYSTEM_PROMPT = """You help candidates prepare for a specific interview round using ONLY real questions from their saved interview history.

Rules:
- Select questions only from the provided past-interview bank list
- Use the exact question wording from the bank — do not paraphrase or invent new questions
- Never create questions from the job description or general interview knowledge
- If the bank has fewer suitable questions than requested, return only what the bank supports
- Prefer same-step questions (higher times_seen) when choosing
- Each question must assess a DIFFERENT topic or skill — never rephrase the same question
- Every question must use one of the allowed category labels exactly
- Set source to "past_interview" for every question
- Return valid JSON only"""


def _category_labels(categories: list[PrepQuestionCategory]) -> list[str]:
    return [PREP_CATEGORY_LABELS[category] for category in categories]


def _finalize_predicted_questions(
    questions: list[PredictedQuestion],
    *,
    question_count: int,
    categories: list[PrepQuestionCategory],
) -> list[PredictedQuestion]:
    allowed = set(categories)
    deduped = dedupe_predicted_questions(questions)
    filtered = [question for question in deduped if question.category in allowed]
    return filtered[:question_count]


def _append_shortfall_notice(summary: str, returned: int, requested: int, available: int) -> str:
    if returned >= requested:
        return summary
    notice = (
        f"Returned {returned} of {requested} requested questions — only {available} unique "
        "question(s) are available in your saved interview bank for this round. "
        "Analyze more recordings to grow the bank; new questions are not invented from the job description."
    )
    return f"{summary.rstrip()} {notice}".strip()


class InterviewPrepError(Exception):
    pass


def _empty_prep_result(
    *,
    role_title: str,
    role_description: str,
    interview_step: InterviewStep,
    company: str | None,
    bank,
    question_count: int,
    question_categories: list[PrepQuestionCategory],
    saved_job_id: str | None,
) -> InterviewPrepResult:
    step_label = INTERVIEW_STEP_LABELS[interview_step]
    if bank.saved_interviews_scanned == 0:
        summary = (
            "No saved interviews found yet. Analyze recordings first to build a question bank, "
            f"then return here to prepare for your {step_label}."
        )
    else:
        summary = (
            f"No practice questions were found in your saved bank for a {step_label}. "
            "Analyze more interviews from this round to add questions — "
            "we do not invent questions from the job description."
        )

    return InterviewPrepResult(
        role_title=role_title,
        role_description=role_description,
        interview_step=interview_step,
        company=company,
        saved_interviews_used=bank.saved_interviews_scanned,
        matching_step_interviews_used=bank.matching_step_interviews,
        past_questions_reviewed=bank.total_past_questions_in_bank,
        unique_past_questions_used=0,
        available_bank_questions=0,
        prep_summary=summary,
        predicted_questions=[],
        focus_areas=[],
        saved_job_id=saved_job_id,
        requested_question_count=question_count,
        requested_categories=question_categories,
    )


def prepare_for_interview(request: InterviewPrepRequest) -> InterviewPrepResult:
    if not settings.openai_api_key:
        raise InterviewPrepError("OPENAI_API_KEY is not configured")

    role_title = request.role_title.strip()
    role_description = request.role_description.strip()
    company = request.company.strip() if request.company else None
    interview_step = request.interview_step

    if request.job_id:
        saved_job = job_store.get(request.job_id)
        if not saved_job:
            raise InterviewPrepError(f"Saved job description not found: {request.job_id}")
        role_title = saved_job.role_title
        role_description = saved_job.role_description
        company = saved_job.company

    saved_job_id = None
    if request.save_job_description:
        saved_job = job_store.save(role_title, role_description, company=company)
        saved_job_id = saved_job.job_id

    saved_payloads = result_store.list_all_saved()
    bank = build_prep_bank_selection(
        saved_payloads,
        interview_step,
        role_title=role_title,
        role_description=role_description,
    )

    step_label = INTERVIEW_STEP_LABELS[interview_step]
    question_count = request.question_count
    question_categories = list(request.question_categories)
    category_values = [category.value for category in question_categories]
    category_labels = _category_labels(question_categories)
    bank_pool = bank.ranked_bank_questions
    available_bank_questions = len(bank_pool)

    if not bank_pool:
        return _empty_prep_result(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            bank=bank,
            question_count=question_count,
            question_categories=question_categories,
            saved_job_id=saved_job_id,
        )

    pool_for_prompt = bank_pool[:MAX_BANK_QUESTIONS_IN_PROMPT]
    client = OpenAI(api_key=settings.openai_api_key)
    user_prompt = f"""Prepare interview practice questions for this upcoming interview round.

Target role title: {role_title}
Target company: {company or "Not specified"}
Target interview step: {interview_step.value}
Target interview step label: {step_label}

Question preferences:
- Select up to {question_count} unique questions
- Allowed categories only: {", ".join(category_values)}
- Category labels for the user: {", ".join(category_labels)}
- Spread selections across the selected categories when possible

Past question bank stats:
- Saved recordings scanned: {bank.saved_interviews_scanned}
- Same-step recordings: {bank.matching_step_interviews}
- Total past questions in bank: {bank.total_past_questions_in_bank}
- Unique questions available to select: {available_bank_questions}

Common topics from same-step interviews:
{json.dumps(bank.matching_topics, indent=2) if bank.matching_topics else "[]"}

ALLOWED QUESTIONS — select ONLY from this list (use exact wording):
{json.dumps(pool_for_prompt, indent=2)}

Return JSON:
{{
  "prep_summary": "2-4 sentences focused on what to expect in this specific round",
  "focus_areas": ["area1", "area2"],
  "predicted_questions": [
    {{
      "question": "must match a question from ALLOWED QUESTIONS exactly",
      "category": "one of: {'|'.join(category_values)}",
      "why_likely": "why this real past question fits this interview step",
      "source": "past_interview",
      "based_on_role": "role title from a past interview if relevant, else null",
      "preparation_tips": ["tip1", "tip2"],
      "strong_answer_outline": "how to structure a strong answer for this step"
    }}
  ]
}}

Rules:
- Questions must fit the target step: {step_label}
- NEVER invent or paraphrase questions — only use text from ALLOWED QUESTIONS
- NEVER use the job description to create new questions
- Prefer questions with higher times_seen when choosing
- Return at most {question_count} questions, and fewer if the bank does not have enough distinct matches
- Use only these categories: {", ".join(category_values)}
- source must always be "past_interview"
"""

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": PREP_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except Exception as exc:
        raise InterviewPrepError(f"Interview prep failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise InterviewPrepError("Interview prep returned empty response")

    try:
        data = json.loads(content)
        predicted = [PredictedQuestion.model_validate(item) for item in data["predicted_questions"]]
        predicted = restrict_to_bank_questions(predicted, bank_pool)
        before_count = len(predicted)
        predicted = _finalize_predicted_questions(
            predicted,
            question_count=question_count,
            categories=question_categories,
        )
        if before_count > len(predicted):
            logger.info(
                "Prepared %d bank-only questions after filter/dedupe (from %d model outputs)",
                len(predicted),
                before_count,
            )

        prep_summary = _append_shortfall_notice(
            data["prep_summary"],
            len(predicted),
            question_count,
            available_bank_questions,
        )

        result = InterviewPrepResult(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            saved_interviews_used=bank.saved_interviews_scanned,
            matching_step_interviews_used=bank.matching_step_interviews,
            past_questions_reviewed=bank.total_past_questions_in_bank,
            unique_past_questions_used=len(pool_for_prompt),
            available_bank_questions=available_bank_questions,
            prep_summary=prep_summary,
            predicted_questions=predicted,
            focus_areas=data.get("focus_areas", []),
            saved_job_id=saved_job_id,
            requested_question_count=question_count,
            requested_categories=question_categories,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise InterviewPrepError(f"Failed to parse interview prep result: {exc}") from exc

    logger.info(
        "Prepared %d bank-only questions for step %s (%d available in bank, %d requested)",
        len(result.predicted_questions),
        interview_step.value,
        available_bank_questions,
        question_count,
    )
    return result
