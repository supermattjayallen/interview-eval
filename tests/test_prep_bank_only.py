from app.models import PredictedQuestion
from app.prep_categories import PrepQuestionCategory
from app.services.prep_retrieval import (
    canonical_bank_question,
    normalize_question,
    restrict_to_bank_questions,
    select_bank_questions,
)

def test_select_bank_questions_returns_ranked_limit():
    pool = [{"question": f"Question {index}"} for index in range(20)]
    assert len(select_bank_questions(pool, 10)) == 10
    assert select_bank_questions(pool, 10)[0]["question"] == "Question 0"
    assert len(select_bank_questions(pool, 25)) == 20


def test_build_predicted_from_polished_uses_display_question():
    from app.services.interview_prep import _build_predicted_from_polished
    from app.db.prep_question_store import PolishedPrepItem

    polished = [
        PolishedPrepItem(
            original_question="uh so like tell me about yourself",
            display_question="Tell me about yourself.",
            question_normalized="tell me about yourself",
            category=PrepQuestionCategory.BEHAVIORAL,
            based_on_role=None,
            times_seen=3,
            topics=[],
        )
    ]
    predicted = _build_predicted_from_polished(polished, categories=list(PrepQuestionCategory))
    assert predicted[0].question == "Tell me about yourself."
    assert predicted[0].original_question == "uh so like tell me about yourself"


def test_build_predicted_from_enrichment_fills_all_selected():
    from app.services.interview_prep import _default_predicted_question

    categories = list(PrepQuestionCategory)
    predicted = [_default_predicted_question(f"Question {index}", categories=categories) for index in range(10)]
    assert len(predicted) == 10


def test_shortfall_notice_only_when_bank_is_smaller_than_requested():
    from app.services.interview_prep import _append_shortfall_notice

    assert "only 5 unique" in _append_shortfall_notice("Summary.", 5, 10, 5)
    assert "prep catalog" in _append_shortfall_notice("Summary.", 5, 10, 5)
    assert _append_shortfall_notice("Summary.", 5, 10, 214) == "Summary."


def test_canonical_bank_question_matches_paraphrase():
    pool = [{"question": "Tell me about yourself and your background."}]
    assert (
        canonical_bank_question("Can you tell me about yourself?", pool)
        == "Tell me about yourself and your background."
    )


def test_restrict_to_bank_questions_drops_invented_questions():
    pool = [
        {"question": "How do you prioritize tasks when everything is urgent?"},
        {"question": "Describe your experience with Kubernetes."},
    ]
    predicted = [
        PredictedQuestion(
            question="How do you prioritize tasks when everything is urgent?",
            category=PrepQuestionCategory.BEHAVIORAL,
            source="past_interview",
        ),
        PredictedQuestion(
            question="What is your approach to designing a rate limiter from scratch?",
            category=PrepQuestionCategory.TECHNICAL,
            source="job_description",
        ),
    ]

    kept = restrict_to_bank_questions(predicted, pool)
    assert len(kept) == 1
    assert kept[0].question == pool[0]["question"]
    assert kept[0].source == "past_interview"


def test_normalize_question_strips_punctuation():
    assert normalize_question("Hello, world!") == "hello world"
