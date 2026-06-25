import logging
from datetime import datetime, timezone

import json
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.db.models import Question, Recording, SampleAnswer
from app.db.session import database_enabled, get_session
from app.models import InterviewAnalysisRequest, InterviewAnalysisResult
from app.services.prep_retrieval import normalize_question

logger = logging.getLogger(__name__)


def _scalar_value(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


class QuestionBankStore:
    def is_enabled(self) -> bool:
        return database_enabled()

    def upsert_analysis(
        self,
        request: InterviewAnalysisRequest,
        result: InterviewAnalysisResult,
        *,
        recording_key: str,
        normalized_recording_id: str,
        analyzed_at: datetime | None = None,
    ) -> None:
        if not self.is_enabled():
            return

        analyzed_at = analyzed_at or datetime.now(timezone.utc)
        if analyzed_at.tzinfo is None:
            analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)

        session = get_session()
        try:
            recording = session.scalar(
                select(Recording).where(Recording.recording_key == recording_key)
            )
            if recording is None:
                recording = Recording(recording_key=recording_key)
                session.add(recording)

            recording.normalized_recording_id = normalized_recording_id
            recording.recording_url = str(request.recording_url)
            recording.role_title = request.role_title
            recording.role_description = request.role_description
            recording.interview_step = _scalar_value(result.interview_step)
            recording.interview_step_inferred = result.interview_step_inferred
            recording.transcript_summary = result.transcript_summary
            recording.evaluation_skipped = result.evaluation_skipped
            recording.topics_covered = list(result.topics_covered or [])
            recording.analyzed_at = analyzed_at

            session.flush()
            session.execute(delete(Question).where(Question.recording_id == recording.id))
            session.flush()

            for index, qa in enumerate(result.qa_pairs):
                question_text = str(qa.question or "").strip()
                question = Question(
                    recording_id=recording.id,
                    question_index=index,
                    question_text=question_text,
                    question_normalized=normalize_question(question_text),
                    question_timestamp=qa.question_timestamp,
                    candidate_answer=qa.answer or "",
                    answer_timestamp=qa.answer_timestamp,
                    category=None,
                    quality=_scalar_value(qa.quality),
                    score=qa.score,
                    strengths=list(qa.strengths or []),
                    gaps=list(qa.gaps or []),
                )
                session.add(question)
                session.flush()

                ideal_answer = (qa.ideal_answer or "").strip()
                ideal_points = list(qa.ideal_answer_points or [])
                if ideal_answer or ideal_points:
                    session.add(
                        SampleAnswer(
                            question_id=question.id,
                            ideal_answer=ideal_answer,
                            ideal_answer_points=ideal_points,
                            source="analysis",
                            updated_at=analyzed_at,
                        )
                    )

            session.commit()
            logger.info(
                "Persisted %d questions for recording %s to PostgreSQL",
                len(result.qa_pairs),
                recording_key,
            )
        except Exception:
            session.rollback()
            logger.exception("Failed to persist analysis %s to PostgreSQL", recording_key)
            raise
        finally:
            session.close()

    def upsert_from_payload(self, payload: dict) -> str | None:
        request_data = payload.get("request") or {}
        result_data = payload.get("result") or {}
        recording_key = payload.get("recording_key")
        normalized_recording_id = payload.get("normalized_recording_id")
        if not recording_key or not normalized_recording_id:
            return None

        request = InterviewAnalysisRequest.model_validate(request_data)
        result = InterviewAnalysisResult.model_validate(result_data)
        saved_at_raw = payload.get("saved_at")
        analyzed_at = None
        if saved_at_raw:
            analyzed_at = datetime.fromisoformat(str(saved_at_raw).replace("Z", "+00:00"))

        self.upsert_analysis(
            request,
            result,
            recording_key=recording_key,
            normalized_recording_id=normalized_recording_id,
            analyzed_at=analyzed_at,
        )
        return recording_key

    def update_sample_answer(
        self,
        recording_key: str,
        question_index: int,
        *,
        ideal_answer: str,
        ideal_answer_points: list[str],
        source: str = "regenerated",
    ) -> bool:
        if not self.is_enabled():
            return False

        session = get_session()
        try:
            recording = session.scalar(
                select(Recording)
                .where(Recording.recording_key == recording_key)
                .options(selectinload(Recording.questions).selectinload(Question.sample_answer))
            )
            if recording is None:
                return False

            question = next(
                (item for item in recording.questions if item.question_index == question_index),
                None,
            )
            if question is None:
                return False

            now = datetime.now(timezone.utc)
            if question.sample_answer is None:
                question.sample_answer = SampleAnswer(
                    question_id=question.id,
                    ideal_answer=ideal_answer,
                    ideal_answer_points=ideal_answer_points,
                    source=source,
                    updated_at=now,
                )
            else:
                question.sample_answer.ideal_answer = ideal_answer
                question.sample_answer.ideal_answer_points = ideal_answer_points
                question.sample_answer.source = source
                question.sample_answer.updated_at = now

            session.commit()
            return True
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to update sample answer for %s question %d",
                recording_key,
                question_index,
            )
            raise
        finally:
            session.close()

    def list_saved_payloads(self) -> list[dict]:
        if not self.is_enabled():
            return []

        session = get_session()
        try:
            recordings = session.scalars(
                select(Recording)
                .options(
                    selectinload(Recording.questions).selectinload(Question.sample_answer),
                )
                .order_by(Recording.analyzed_at.desc())
            ).all()
            return [_recording_to_payload(recording) for recording in recordings]
        finally:
            session.close()

    def count_recordings(self) -> int:
        if not self.is_enabled():
            return 0

        session = get_session()
        try:
            return session.scalar(select(func.count()).select_from(Recording)) or 0
        finally:
            session.close()

    def backfill_from_directory(self, results_dir: str | Path) -> dict[str, int]:
        results_path = Path(results_dir)
        stats = {"files": 0, "imported": 0, "skipped": 0, "errors": 0}

        for path in sorted(results_path.glob("interview-analysis-*.json")):
            stats["files"] += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if self.upsert_from_payload(payload):
                    stats["imported"] += 1
                else:
                    stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1
                logger.exception("Backfill failed for %s", path.name)

        return stats


def _recording_to_payload(recording: Recording) -> dict:
    qa_pairs = []
    for question in recording.questions:
        sample = question.sample_answer
        qa_pairs.append(
            {
                "question": question.question_text,
                "question_timestamp": question.question_timestamp,
                "answer": question.candidate_answer,
                "answer_timestamp": question.answer_timestamp,
                "quality": question.quality,
                "score": question.score,
                "strengths": list(question.strengths or []),
                "gaps": list(question.gaps or []),
                "ideal_answer": sample.ideal_answer if sample else "",
                "ideal_answer_points": list(sample.ideal_answer_points or []) if sample else [],
            }
        )

    return {
        "recording_key": recording.recording_key,
        "normalized_recording_id": recording.normalized_recording_id,
        "saved_at": recording.analyzed_at.isoformat(),
        "request": {
            "recording_url": recording.recording_url,
            "role_title": recording.role_title,
            "role_description": recording.role_description,
            "interview_step": recording.interview_step,
        },
        "result": {
            "recording_url": recording.recording_url,
            "interview_step": recording.interview_step,
            "interview_step_inferred": recording.interview_step_inferred,
            "transcript_summary": recording.transcript_summary,
            "evaluation_skipped": recording.evaluation_skipped,
            "topics_covered": list(recording.topics_covered or []),
            "qa_pairs": qa_pairs,
        },
    }


question_bank_store = QuestionBankStore()
