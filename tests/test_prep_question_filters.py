from app.services.prep_question_filters import is_prep_worthy_question
from app.services.prep_retrieval import select_diverse_ranked


def test_is_prep_worthy_rejects_team_size_questions():
    assert not is_prep_worthy_question("How many people were on your team?")
    assert not is_prep_worthy_question("What was the team size on that project?")


def test_is_prep_worthy_accepts_real_interview_questions():
    assert is_prep_worthy_question("Tell me about a time you led a difficult project.")
    assert is_prep_worthy_question("How would you design a rate limiter for an API?")


def test_select_diverse_ranked_avoids_near_duplicates():
    class Row:
        def __init__(self, question: str) -> None:
            self.question = question

    ranked = [
        (100.0, Row("Tell me about yourself.")),
        (99.0, Row("Walk me through your background.")),
        (98.0, Row("Explain Kubernetes networking.")),
        (97.0, Row("How do you design a cache?")),
    ]
    picked = select_diverse_ranked(ranked, limit=3, question_text=lambda row: row.question)
    texts = [row.question for row in picked]
    assert len(picked) == 3
    assert not (
        "Tell me about yourself." in texts and "Walk me through your background." in texts
    )
