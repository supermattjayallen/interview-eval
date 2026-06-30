import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from openai import OpenAI
from sqlalchemy import func, select

from app.config import settings
from app.db.models import PrepQuestion, Question, Recording
from app.db.session import database_enabled, get_session
from app.interview_steps import InterviewStep
from app.prep_categories import PrepQuestionCategory, default_prep_categories, normalize_prep_category
from app.services.prep_question_filters import is_prep_worthy_question
from app.services.prep_retrieval import (
    compute_relevance_score,
    normalize_question,
    select_diverse_ranked,
)
from app.services.prep_question_filters import is_prep_worthy_question

logger = logging.getLogger(__name__)

POLISH_BATCH_SIZE = 12

POLISH_SYSTEM_PROMPT = """You build interview-prep questions from raw transcript extractions.

Rules:
- Rewrite each raw question into a clear, natural question a real interviewer would ask
- Preserve intent and topic — do not invent new subjects
- Remove filler words, false starts, and transcript artifacts
- Skip logistics, scheduling, team-size headcount, and audio/video check questions
- Return enrichment for EVERY input question — same count
- Include original_question exactly as provided for mapping
- Assign the best matching category from the full allowed list
- Return valid JSON only"""


@dataclass
class BankAggregate:
    question_normalized: str
    original_question: str
    times_seen: int = 0
    score_total: float = 0.0
    score_count: int = 0
    topics: set[str] = field(default_factory=set)
    source_question_ids: list[int] = field(default_factory=list)
    interview_steps: set[str] = field(default_factory=set)
    role_titles: set[str] = field(default_factory=set)

    @property
    def avg_score(self) -> float | None:
        if self.score_count == 0:
            return None
        return round(self.score_total / self.score_count, 1)

    def to_polish_payload(self) -> dict:
        payload = {
            "question": self.original_question,
            "times_seen": self.times_seen,
        }
        if self.avg_score is not None:
            payload["avg_score"] = self.avg_score
        if self.topics:
            payload["topics"] = sorted(self.topics)[:8]
        return payload


@dataclass
class PolishedPrepItem:
    original_question: str
    display_question: str
    question_normalized: str
    category: PrepQuestionCategory
    based_on_role: str | None
    times_seen: int
    topics: list[str]
    interview_steps: list[str] = field(default_factory=list)
    avg_score: float | None = None


@dataclass
class PrepCatalogSelection:
    saved_interviews_scanned: int
    matching_step_interviews: int
    other_step_interviews: int
    total_past_questions_in_bank: int
    available_prep_questions: int
    matching_topics: list[str]
    items: list[PolishedPrepItem]


class PrepQuestionStore:
    def is_enabled(self) -> bool:
        return database_enabled()

    def rebuild_after_recording(self, recording_id: int, *, force_polish: bool = False) -> dict[str, int]:
        return self._rebuild(only_recording_id=recording_id, force_polish=force_polish)

    def rebuild_all(self, *, force_polish: bool = False) -> dict[str, int]:
        return self._rebuild(only_recording_id=None, force_polish=force_polish)

    def select_for_prep(
        self,
        *,
        interview_step: InterviewStep,
        role_title: str,
        role_description: str,
        categories: list[PrepQuestionCategory],
        limit: int,
    ) -> PrepCatalogSelection:
        if not self.is_enabled():
            return PrepCatalogSelection(0, 0, 0, 0, 0, [], [])

        allowed = {category.value for category in categories}
        relevance_text = f"{role_title}\n{role_description}"
        session = get_session()
        try:
            stats = self._recording_stats(session, interview_step=interview_step)
            rows = session.scalars(select(PrepQuestion)).all()
            eligible = [
                row
                for row in rows
                if (not row.category or row.category in allowed)
                and is_prep_worthy_question(row.display_question or row.original_question)
            ]
            ranked: list[tuple[float, PrepQuestion]] = []
            for row in eligible:
                step_bonus = 0.0
                steps = set(row.interview_steps or [])
                if row.interview_step == interview_step.value:
                    step_bonus = 20.0
                elif interview_step.value in steps:
                    step_bonus = 10.0
                relevance = compute_relevance_score(
                    row.display_question,
                    row.topics or [],
                    relevance_text,
                    int(row.times_seen or 1),
                )
                ranked.append((step_bonus + relevance, row))

            ranked.sort(key=lambda item: item[0], reverse=True)
            selected_rows = select_diverse_ranked(
                ranked,
                limit=limit,
                question_text=lambda row: row.display_question or row.original_question,
            )
            items = [self._row_to_item(row) for row in selected_rows]

            topic_counts: Counter[str] = Counter()
            for row in rows:
                if row.interview_step == interview_step.value or interview_step.value in (
                    row.interview_steps or []
                ):
                    for topic in row.topics or []:
                        if topic:
                            topic_counts[topic] += 1

            return PrepCatalogSelection(
                saved_interviews_scanned=stats["recordings"],
                matching_step_interviews=stats["matching_step_interviews"],
                other_step_interviews=stats["other_step_interviews"],
                total_past_questions_in_bank=stats["raw_questions"],
                available_prep_questions=len(eligible),
                matching_topics=[
                    topic for topic, _count in topic_counts.most_common(settings.prep_max_topics)
                ],
                items=items,
            )
        finally:
            session.close()

    def generate_prep_brief(
        self,
        client: OpenAI,
        *,
        items: list[PolishedPrepItem],
        step_label: str,
        role_title: str,
        role_description: str,
        company: str | None,
        matching_topics: list[str],
    ) -> tuple[str, list[str]]:
        if not items:
            return "", []

        questions = [
            {
                "question": item.display_question,
                "category": item.category.value,
                "times_seen": item.times_seen,
            }
            for item in items
        ]
        user_prompt = f"""Write a short prep brief for an upcoming {step_label}.

Role: {role_title}
Company: {company or "Not specified"}

Job description:
{role_description[:4000]}

Topics from saved interviews:
{json.dumps(matching_topics, indent=2) if matching_topics else "[]"}

Selected practice questions:
{json.dumps(questions, indent=2)}

Return JSON:
{{
  "prep_summary": "2-4 sentences on what to expect in this round",
  "focus_areas": ["area1", "area2"]
}}"""

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "You write concise interview preparation briefs. Return valid JSON only.",
                },
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        if not content:
            return "", []
        data = json.loads(content)
        return str(data.get("prep_summary") or ""), list(data.get("focus_areas") or [])

    def _rebuild(self, *, only_recording_id: int | None, force_polish: bool) -> dict[str, int]:
        if not self.is_enabled():
            return {"aggregates": 0, "polished": 0, "skipped": 0, "errors": 0}

        stats = {"aggregates": 0, "polished": 0, "skipped": 0, "errors": 0}
        to_polish: list[BankAggregate] = []
        session = get_session()
        try:
            aggregates = self._aggregate_questions(session)
            stats["aggregates"] = len(aggregates)

            if only_recording_id is not None:
                recording = session.get(Recording, only_recording_id)
                if recording is None:
                    return stats
                keys = {
                    question.question_normalized
                    for question in recording.questions
                    if question.question_normalized
                }
                aggregates = {key: aggregates[key] for key in keys if key in aggregates}

            to_polish = []
            now = self._utcnow()
            for aggregate in aggregates.values():
                row = session.scalar(
                    select(PrepQuestion).where(
                        PrepQuestion.question_normalized == aggregate.question_normalized
                    )
                )
                self._sync_aggregate_stats(row, aggregate, now=now, session=session)
                if force_polish or row is None or self._needs_polish(row, aggregate):
                    to_polish.append(aggregate)
                else:
                    stats["skipped"] += 1

            session.commit()
        except Exception:
            session.rollback()
            stats["errors"] += 1
            logger.exception("Prep catalog rebuild failed while syncing aggregates")
            raise
        finally:
            session.close()

        if not to_polish:
            return stats

        if not settings.openai_api_key:
            logger.warning("OPENAI_API_KEY missing — prep questions saved without polish")
            self._persist_unpolished(to_polish)
            return stats

        client = OpenAI(api_key=settings.openai_api_key)
        category_values = [category.value for category in default_prep_categories()]
        for start in range(0, len(to_polish), POLISH_BATCH_SIZE):
            batch = to_polish[start : start + POLISH_BATCH_SIZE]
            try:
                polished = self._polish_batch(client, batch, category_values=category_values)
                self._persist_polished(polished, aggregates=batch)
                stats["polished"] += len(polished)
            except Exception:
                stats["errors"] += len(batch)
                logger.exception("Prep polish batch failed (%d items)", len(batch))
                self._persist_unpolished(batch)

        return stats

    def _aggregate_questions(self, session) -> dict[str, BankAggregate]:
        rows = session.execute(
            select(Question, Recording).join(Recording, Question.recording_id == Recording.id)
        ).all()

        aggregates: dict[str, BankAggregate] = {}
        for question, recording in rows:
            question_text = str(question.question_text or "").strip()
            if not question_text or not is_prep_worthy_question(question_text):
                continue

            key = question.question_normalized or normalize_question(question_text)
            if not key:
                continue

            entry = aggregates.get(key)
            if entry is None:
                entry = BankAggregate(question_normalized=key, original_question=question_text)
                aggregates[key] = entry

            if len(question_text) > len(entry.original_question):
                entry.original_question = question_text

            entry.times_seen += 1
            entry.source_question_ids.append(question.id)
            if question.score is not None:
                entry.score_total += float(question.score)
                entry.score_count += 1
            if recording.interview_step:
                entry.interview_steps.add(recording.interview_step)
            if recording.role_title:
                entry.role_titles.add(recording.role_title)
            for topic in recording.topics_covered or []:
                if topic:
                    entry.topics.add(str(topic))

        return aggregates

    def _sync_aggregate_stats(
        self,
        row: PrepQuestion | None,
        aggregate: BankAggregate,
        *,
        now,
        session,
    ) -> None:
        primary_step = self._primary_step(aggregate.interview_steps)
        based_on_role = next(iter(sorted(aggregate.role_titles)), None)

        if row is None:
            row = PrepQuestion(
                question_normalized=aggregate.question_normalized,
                original_question=aggregate.original_question,
                display_question=aggregate.original_question,
                polished_at=now,
            )
            session.add(row)

        row.original_question = aggregate.original_question
        row.times_seen = aggregate.times_seen
        row.avg_score = aggregate.avg_score
        row.topics = sorted(aggregate.topics)
        row.interview_steps = sorted(aggregate.interview_steps)
        row.interview_step = primary_step
        row.source_question_ids = sorted(set(aggregate.source_question_ids))
        row.based_on_role = based_on_role
        row.updated_at = now

    def _needs_polish(self, row: PrepQuestion, aggregate: BankAggregate) -> bool:
        if row.original_question != aggregate.original_question:
            return True
        if not row.display_question or row.display_question == row.original_question:
            return True
        if not row.category:
            return True
        return False

    def _polish_batch(
        self,
        client: OpenAI,
        aggregates: list[BankAggregate],
        *,
        category_values: list[str],
    ) -> list[PolishedPrepItem]:
        bank_items = [aggregate.to_polish_payload() for aggregate in aggregates]
        user_prompt = f"""Polish these raw transcript questions for interview preparation.

Allowed categories: {", ".join(category_values)}

RAW QUESTIONS (return all {len(bank_items)}):
{json.dumps(bank_items, indent=2)}

Return JSON:
{{
  "questions": [
    {{
      "original_question": "exact raw question from input",
      "display_question": "clear polished interviewer wording",
      "category": "one of: {'|'.join(category_values)}"
    }}
  ]
}}"""

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": POLISH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Prep polish returned empty response")

        by_original = {
            str(item.get("original_question", "")).strip(): item
            for item in json.loads(content).get("questions") or []
        }

        polished: list[PolishedPrepItem] = []
        for aggregate in aggregates:
            enrichment = by_original.get(aggregate.original_question, {})
            display = str(enrichment.get("display_question") or aggregate.original_question).strip()
            polished.append(
                PolishedPrepItem(
                    original_question=aggregate.original_question,
                    display_question=display or aggregate.original_question,
                    question_normalized=aggregate.question_normalized,
                    category=normalize_prep_category(enrichment.get("category")),
                    based_on_role=next(iter(sorted(aggregate.role_titles)), None),
                    times_seen=aggregate.times_seen,
                    topics=sorted(aggregate.topics),
                    interview_steps=sorted(aggregate.interview_steps),
                    avg_score=aggregate.avg_score,
                )
            )
        return polished

    def _persist_polished(
        self,
        items: list[PolishedPrepItem],
        *,
        aggregates: list[BankAggregate],
    ) -> None:
        aggregate_by_key = {item.question_normalized: item for item in aggregates}
        now = self._utcnow()
        session = get_session()
        try:
            for item in items:
                aggregate = aggregate_by_key.get(item.question_normalized)
                row = session.scalar(
                    select(PrepQuestion).where(
                        PrepQuestion.question_normalized == item.question_normalized
                    )
                )
                if row is None:
                    row = PrepQuestion(
                        question_normalized=item.question_normalized,
                        polished_at=now,
                    )
                    session.add(row)

                row.original_question = item.original_question
                row.display_question = item.display_question
                row.category = item.category.value
                row.why_likely = None
                row.preparation_tips = []
                row.strong_answer_outline = None
                row.based_on_role = item.based_on_role
                row.times_seen = item.times_seen
                row.topics = item.topics
                row.interview_steps = item.interview_steps
                row.interview_step = self._primary_step(item.interview_steps)
                row.avg_score = item.avg_score
                if aggregate:
                    row.source_question_ids = sorted(set(aggregate.source_question_ids))
                row.updated_at = now
                if not row.polished_at:
                    row.polished_at = now

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _persist_unpolished(self, aggregates: list[BankAggregate]) -> None:
        now = self._utcnow()
        session = get_session()
        try:
            for aggregate in aggregates:
                row = session.scalar(
                    select(PrepQuestion).where(
                        PrepQuestion.question_normalized == aggregate.question_normalized
                    )
                )
                if row is None:
                    row = PrepQuestion(
                        question_normalized=aggregate.question_normalized,
                        polished_at=now,
                    )
                    session.add(row)
                row.original_question = aggregate.original_question
                row.display_question = aggregate.original_question
                row.times_seen = aggregate.times_seen
                row.topics = sorted(aggregate.topics)
                row.interview_steps = sorted(aggregate.interview_steps)
                row.interview_step = self._primary_step(aggregate.interview_steps)
                row.avg_score = aggregate.avg_score
                row.source_question_ids = sorted(set(aggregate.source_question_ids))
                row.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _recording_stats(self, session, *, interview_step: InterviewStep) -> dict[str, int]:
        recordings = session.scalars(select(Recording)).all()
        raw_questions = session.scalar(select(func.count()).select_from(Question)) or 0
        matching = sum(1 for recording in recordings if recording.interview_step == interview_step.value)
        return {
            "recordings": len(recordings),
            "raw_questions": int(raw_questions),
            "matching_step_interviews": matching,
            "other_step_interviews": max(len(recordings) - matching, 0),
        }

    def _row_to_item(self, row: PrepQuestion) -> PolishedPrepItem:
        return PolishedPrepItem(
            original_question=row.original_question,
            display_question=row.display_question,
            question_normalized=row.question_normalized,
            category=normalize_prep_category(row.category or PrepQuestionCategory.OTHER),
            based_on_role=row.based_on_role,
            times_seen=int(row.times_seen or 1),
            topics=list(row.topics or []),
            interview_steps=list(row.interview_steps or []),
            avg_score=row.avg_score,
        )

    @staticmethod
    def _primary_step(steps: set[str] | list[str]) -> str | None:
        if not steps:
            return None
        return sorted(steps)[0]

    @staticmethod
    def _utcnow():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)

    def count_prep_questions(self) -> int:
        if not self.is_enabled():
            return 0
        session = get_session()
        try:
            return session.scalar(select(func.count()).select_from(PrepQuestion)) or 0
        finally:
            session.close()


prep_question_store = PrepQuestionStore()
