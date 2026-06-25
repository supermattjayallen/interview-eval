from app.models import PredictedQuestion
from app.prep_categories import PrepQuestionCategory
from app.services.prep_retrieval import (
    canonical_bank_question,
    normalize_question,
    restrict_to_bank_questions,
)


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
            why_likely="Common in screens",
            source="past_interview",
        ),
        PredictedQuestion(
            question="What is your approach to designing a rate limiter from scratch?",
            category=PrepQuestionCategory.TECHNICAL,
            why_likely="Invented from JD",
            source="job_description",
        ),
    ]

    kept = restrict_to_bank_questions(predicted, pool)
    assert len(kept) == 1
    assert kept[0].question == pool[0]["question"]
    assert kept[0].source == "past_interview"


def test_normalize_question_strips_punctuation():
    assert normalize_question("Hello, world!") == "hello world"
