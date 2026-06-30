import logging

from openai import OpenAI

from app.config import settings
from app.db.prep_question_store import PolishedPrepItem, prep_question_store
from app.db.question_bank import question_bank_store
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep
from app.models import InterviewPrepRequest, InterviewPrepResult, PredictedQuestion
from app.prep_categories import PrepQuestionCategory, normalize_prep_category
from app.services.prep_retrieval import (
    PrepBankSelection,
    build_prep_bank_selection,
    dedupe_predicted_questions,
    select_bank_questions,
)
from app.services.result_store import result_store

logger = logging.getLogger(__name__)


def _coerce_allowed_category(
    value,
    allowed: set[PrepQuestionCategory],
) -> PrepQuestionCategory:
    category = normalize_prep_category(value)
    if category in allowed:
        return category
    if PrepQuestionCategory.OTHER in allowed:
        return PrepQuestionCategory.OTHER
    return next(iter(allowed))


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


def _build_predicted_from_polished(
    polished: list[PolishedPrepItem],
    *,
    categories: list[PrepQuestionCategory],
) -> list[PredictedQuestion]:
    allowed = set(categories)
    predicted: list[PredictedQuestion] = []
    for item in polished:
        category = _coerce_allowed_category(item.category, allowed)
        predicted.append(
            PredictedQuestion(
                question=item.display_question,
                original_question=item.original_question
                if item.original_question != item.display_question
                else None,
                category=category,
                source="past_interview",
                based_on_role=item.based_on_role,
            )
        )
    return predicted


def _default_predicted_question(
    question: str,
    *,
    categories: list[PrepQuestionCategory],
) -> PredictedQuestion:
    return PredictedQuestion(
        question=question,
        category=_coerce_allowed_category(PrepQuestionCategory.OTHER, set(categories)),
        source="past_interview",
    )


def _append_shortfall_notice(summary: str, returned: int, requested: int, available: int) -> str:
    if returned >= requested or available >= requested:
        return summary
    notice = (
        f"Returned {returned} of {requested} requested questions — only {available} unique "
        "question(s) are available in your prep catalog for this round. "
        "Analyze more recordings to grow the catalog."
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
    saved_interviews_scanned: int,
    matching_step_interviews: int,
    total_past_questions_in_bank: int,
    question_count: int,
    question_categories: list[PrepQuestionCategory],
    summary: str | None = None,
) -> InterviewPrepResult:
    step_label = INTERVIEW_STEP_LABELS[interview_step]
    if summary is None:
        if saved_interviews_scanned == 0:
            summary = (
                "No saved interviews found yet. Analyze recordings first to build a question bank, "
                f"then return here to prepare for your {step_label}."
            )
        else:
            summary = (
                f"No practice questions were found in your prep catalog for a {step_label}. "
                "Analyze more interviews from this round or run scripts/rebuild_prep_questions.py."
            )

    return InterviewPrepResult(
        role_title=role_title,
        role_description=role_description,
        interview_step=interview_step,
        company=company,
        saved_interviews_used=saved_interviews_scanned,
        matching_step_interviews_used=matching_step_interviews,
        past_questions_reviewed=total_past_questions_in_bank,
        unique_past_questions_used=0,
        available_bank_questions=0,
        prep_summary=summary,
        predicted_questions=[],
        focus_areas=[],
        requested_question_count=question_count,
        requested_categories=question_categories,
    )


def _prepare_from_prep_catalog(
    *,
    role_title: str,
    role_description: str,
    interview_step: InterviewStep,
    company: str | None,
    question_count: int,
    question_categories: list[PrepQuestionCategory],
) -> InterviewPrepResult:
    step_label = INTERVIEW_STEP_LABELS[interview_step]

    if prep_question_store.count_prep_questions() == 0 and question_bank_store.count_recordings() > 0:
        logger.info("Prep catalog empty with saved recordings — running full rebuild")
        prep_question_store.rebuild_all()

    catalog = prep_question_store.select_for_prep(
        interview_step=interview_step,
        role_title=role_title,
        role_description=role_description,
        categories=question_categories,
        limit=question_count,
    )

    if catalog.saved_interviews_scanned == 0:
        return _empty_prep_result(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            saved_interviews_scanned=0,
            matching_step_interviews=0,
            total_past_questions_in_bank=0,
            question_count=question_count,
            question_categories=question_categories,
        )

    if not catalog.items:
        return _empty_prep_result(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            saved_interviews_scanned=catalog.saved_interviews_scanned,
            matching_step_interviews=catalog.matching_step_interviews,
            total_past_questions_in_bank=catalog.total_past_questions_in_bank,
            question_count=question_count,
            question_categories=question_categories,
            summary=(
                f"No prep questions matched your selected categories for a {step_label}. "
                "Try broader categories or analyze more interviews."
            ),
        )

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        prep_summary, focus_areas = prep_question_store.generate_prep_brief(
            client,
            items=catalog.items,
            step_label=step_label,
            role_title=role_title,
            role_description=role_description,
            company=company,
            matching_topics=catalog.matching_topics,
        )
    except Exception as exc:
        raise InterviewPrepError(f"Interview prep failed: {exc}") from exc

    predicted = _build_predicted_from_polished(catalog.items, categories=question_categories)
    predicted = _finalize_predicted_questions(
        predicted,
        question_count=question_count,
        categories=question_categories,
    )

    prep_summary = _append_shortfall_notice(
        prep_summary,
        len(predicted),
        question_count,
        catalog.available_prep_questions,
    )

    return InterviewPrepResult(
        role_title=role_title,
        role_description=role_description,
        interview_step=interview_step,
        company=company,
        saved_interviews_used=catalog.saved_interviews_scanned,
        matching_step_interviews_used=catalog.matching_step_interviews,
        past_questions_reviewed=catalog.total_past_questions_in_bank,
        unique_past_questions_used=len(predicted),
        available_bank_questions=catalog.available_prep_questions,
        prep_summary=prep_summary,
        predicted_questions=predicted,
        focus_areas=focus_areas,
        requested_question_count=question_count,
        requested_categories=question_categories,
    )


def _prepare_from_json_bank(
    *,
    role_title: str,
    role_description: str,
    interview_step: InterviewStep,
    company: str | None,
    question_count: int,
    question_categories: list[PrepQuestionCategory],
    bank: PrepBankSelection,
) -> InterviewPrepResult:
    step_label = INTERVIEW_STEP_LABELS[interview_step]
    bank_pool = bank.ranked_bank_questions
    available_bank_questions = len(bank_pool)

    if not bank_pool:
        return _empty_prep_result(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            saved_interviews_scanned=bank.saved_interviews_scanned,
            matching_step_interviews=bank.matching_step_interviews,
            total_past_questions_in_bank=bank.total_past_questions_in_bank,
            question_count=question_count,
            question_categories=question_categories,
        )

    selected = select_bank_questions(bank_pool, question_count)
    predicted = [
        _default_predicted_question(str(item["question"]).strip(), categories=question_categories)
        for item in selected
    ]
    predicted = _finalize_predicted_questions(
        predicted,
        question_count=question_count,
        categories=question_categories,
    )

    prep_summary = (
        f"Selected {len(predicted)} practice question(s) from your saved interview bank "
        f"for a {step_label}."
    )
    prep_summary = _append_shortfall_notice(
        prep_summary,
        len(predicted),
        question_count,
        available_bank_questions,
    )

    return InterviewPrepResult(
        role_title=role_title,
        role_description=role_description,
        interview_step=interview_step,
        company=company,
        saved_interviews_used=bank.saved_interviews_scanned,
        matching_step_interviews_used=bank.matching_step_interviews,
        past_questions_reviewed=bank.total_past_questions_in_bank,
        unique_past_questions_used=len(selected),
        available_bank_questions=available_bank_questions,
        prep_summary=prep_summary,
        predicted_questions=predicted,
        focus_areas=list(bank.matching_topics[:5]),
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

    question_count = request.question_count
    question_categories = list(request.question_categories)

    if prep_question_store.is_enabled():
        result = _prepare_from_prep_catalog(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            question_count=question_count,
            question_categories=question_categories,
        )
    else:
        saved_payloads = result_store.list_all_saved()
        bank = build_prep_bank_selection(
            saved_payloads,
            interview_step,
            role_title=role_title,
            role_description=role_description,
        )
        result = _prepare_from_json_bank(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            question_count=question_count,
            question_categories=question_categories,
            bank=bank,
        )

    logger.info(
        "Prepared %d prep questions for step %s (%d available in catalog, %d requested)",
        len(result.predicted_questions),
        interview_step.value,
        result.available_bank_questions,
        question_count,
    )
    return result
