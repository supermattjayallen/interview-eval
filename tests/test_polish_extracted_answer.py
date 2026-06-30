from unittest.mock import MagicMock, patch

import pytest

from app.models import InterviewAnalysisRequest
from app.services.qa_extractor import QAEvaluationError, polish_extracted_answer_for_pair


def test_polish_extracted_answer_requires_answer():
    request = InterviewAnalysisRequest(recording_url="https://example.com/recording")
    with pytest.raises(QAEvaluationError, match="No extracted answer"):
        polish_extracted_answer_for_pair(request, question="Tell me about yourself", answer="")


@patch("app.services.qa_extractor.OpenAI")
def test_polish_extracted_answer_returns_proofread_text(mock_openai):
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content='{"ideal_answer": "I led the Azure migration.", "ideal_answer_points": ["Led Azure migration"]}'
                )
            )
        ]
    )

    request = InterviewAnalysisRequest(recording_url="https://example.com/recording")
    result = polish_extracted_answer_for_pair(
        request,
        question="Describe your cloud experience.",
        answer="So, uh, I led the, like, Azure migration project.",
    )

    assert result["ideal_answer"] == "I led the Azure migration."
    assert result["ideal_answer_points"] == ["Led Azure migration"]

    prompt = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Polish this extracted interview answer" in prompt
    assert "Azure migration" in prompt
