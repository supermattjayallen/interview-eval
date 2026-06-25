from app.services.prep_retrieval import dedupe_predicted_questions, questions_are_similar


class _Question:
    def __init__(self, question: str) -> None:
        self.question = question


def test_questions_are_similar_for_paraphrases():
    assert questions_are_similar(
        "Tell me about your experience with Python.",
        "Can you walk me through your Python experience?",
    )
    assert questions_are_similar(
        "Tell me about yourself.",
        "Walk me through your background and experience.",
    )
    assert questions_are_similar(
        "Why do you want this role?",
        "What attracted you to this position?",
    )


def test_questions_are_not_similar_for_different_topics():
    assert not questions_are_similar(
        "How would you design a rate limiter?",
        "Describe a time you resolved a production incident.",
    )
    assert not questions_are_similar(
        "What is your experience with Kubernetes?",
        "How do you approach database schema migrations?",
    )


def test_dedupe_predicted_questions_keeps_first_occurrence():
    items = [
        _Question("Tell me about yourself."),
        _Question("Walk me through your background and experience."),
        _Question("How would you design a caching layer?"),
        _Question("Design a cache for a high-traffic API."),
    ]
    deduped = dedupe_predicted_questions(items)
    assert len(deduped) == 2
    assert deduped[0].question == "Tell me about yourself."
    assert deduped[1].question == "How would you design a caching layer?"
