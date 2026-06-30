import logging
import re
from dataclasses import dataclass, field

from app.config import settings
from app.interview_steps import InterviewStep
from app.services.prep_question_filters import is_prep_worthy_question

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


@dataclass
class AggregatedQuestion:
    question: str
    times_seen: int = 0
    score_total: float = 0.0
    score_count: int = 0
    topics: set[str] = field(default_factory=set)

    @property
    def avg_score(self) -> float | None:
        if self.score_count == 0:
            return None
        return round(self.score_total / self.score_count, 1)


@dataclass
class PrepBankSelection:
    saved_interviews_scanned: int
    matching_step_interviews: int
    other_step_interviews: int
    total_past_questions_in_bank: int
    unique_matching_questions_in_bank: int
    unique_other_questions_in_bank: int
    matching_questions_for_prompt: list[dict]
    other_questions_for_prompt: list[dict]
    matching_topics: list[str]
    ranked_bank_questions: list[dict] = field(default_factory=list)

    @property
    def unique_past_questions_used(self) -> int:
        return len(self.ranked_bank_questions) or (
            len(self.matching_questions_for_prompt) + len(self.other_questions_for_prompt)
        )


def normalize_question(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


def build_prep_bank_selection(
    saved_payloads: list[dict],
    target_step: InterviewStep,
    role_title: str,
    role_description: str,
) -> PrepBankSelection:
    matching_agg: dict[str, AggregatedQuestion] = {}
    other_agg: dict[str, AggregatedQuestion] = {}
    topic_counts: dict[str, int] = {}

    saved_interviews_scanned = 0
    matching_step_interviews = 0
    other_step_interviews = 0
    total_past_questions_in_bank = 0

    relevance_text = f"{role_title}\n{role_description}"

    for payload in saved_payloads:
        request = payload.get("request", {})
        result = payload.get("result", {})
        qa_pairs = result.get("qa_pairs", [])
        if not qa_pairs:
            continue

        saved_interviews_scanned += 1
        step_value = request.get("interview_step") or result.get("interview_step")
        is_matching = step_value == target_step.value
        bucket = matching_agg if is_matching else other_agg
        interview_topics = result.get("topics_covered") or []

        if is_matching:
            matching_step_interviews += 1
            for topic in interview_topics:
                if topic:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
        else:
            other_step_interviews += 1

        for qa in qa_pairs:
            question = str(qa.get("question", "")).strip()
            if not question or not is_prep_worthy_question(question):
                continue

            total_past_questions_in_bank += 1
            key = normalize_question(question)
            if not key:
                continue

            entry = bucket.get(key)
            if entry is None:
                entry = AggregatedQuestion(question=question)
                bucket[key] = entry

            entry.times_seen += 1
            score = qa.get("score")
            if isinstance(score, (int, float)):
                entry.score_total += float(score)
                entry.score_count += 1
            entry.topics.update(topic for topic in interview_topics if topic)

    matching_questions_for_prompt = _rank_and_limit(
        matching_agg,
        relevance_text,
        settings.prep_max_matching_questions,
    )
    other_questions_for_prompt = _rank_and_limit(
        other_agg,
        relevance_text,
        settings.prep_max_other_questions,
    )
    ranked_bank_questions = _build_ranked_bank_pool(matching_agg, other_agg, relevance_text)
    matching_topics = [
        topic
        for topic, _count in sorted(topic_counts.items(), key=lambda item: item[1], reverse=True)[
            : settings.prep_max_topics
        ]
    ]

    selection = PrepBankSelection(
        saved_interviews_scanned=saved_interviews_scanned,
        matching_step_interviews=matching_step_interviews,
        other_step_interviews=other_step_interviews,
        total_past_questions_in_bank=total_past_questions_in_bank,
        unique_matching_questions_in_bank=len(matching_agg),
        unique_other_questions_in_bank=len(other_agg),
        matching_questions_for_prompt=matching_questions_for_prompt,
        other_questions_for_prompt=other_questions_for_prompt,
        matching_topics=matching_topics,
        ranked_bank_questions=ranked_bank_questions,
    )

    logger.info(
        "Prep bank: scanned %d interviews, %d raw questions, sending %d unique questions to model",
        selection.saved_interviews_scanned,
        selection.total_past_questions_in_bank,
        selection.unique_past_questions_used,
    )
    return selection


def _build_ranked_bank_pool(
    matching_agg: dict[str, AggregatedQuestion],
    other_agg: dict[str, AggregatedQuestion],
    relevance_text: str,
) -> list[dict]:
    """Same-step questions first, then other steps, deduplicated."""
    seen: set[str] = set()
    pool: list[dict] = []
    for aggregated in (matching_agg, other_agg):
        for item in _rank_and_limit(aggregated, relevance_text, limit=10_000):
            key = normalize_question(item["question"])
            if not key or key in seen:
                continue
            seen.add(key)
            pool.append(item)
    return pool


_TECH_FAMILIES: dict[str, tuple[str, ...]] = {
    "azure": ("azure", "azurerm", "cosmos", "aks", "entra", "blob storage", "app service", "functions"),
    "aws": ("aws", "amazon web services", "ec2", "s3", "lambda", "dynamodb", "ecs", "eks", "cloudwatch"),
    "gcp": ("gcp", "google cloud", "bigquery", "gke", "cloud run"),
    "dotnet": (".net", "dotnet", "c#", "csharp", "asp.net", "blazor", "entity framework"),
    "java": ("java", "spring", "spring boot", "jvm", "maven", "gradle"),
    "python": ("python", "django", "flask", "fastapi"),
    "kubernetes": ("kubernetes", "k8s", "helm", "kubectl"),
}


def _mentioned_tech_families(text: str) -> set[str]:
    lowered = text.lower()
    found: set[str] = set()
    for family, terms in _TECH_FAMILIES.items():
        if any(term in lowered for term in terms):
            found.add(family)
    return found


def _stack_alignment_score(question_blob: str, relevance_text: str) -> float:
    """Boost aligned stacks and penalize clear mismatches (e.g. AWS question for Azure JD)."""
    jd_families = _mentioned_tech_families(relevance_text)
    if not jd_families:
        return 0.0

    question_families = _mentioned_tech_families(question_blob)
    if not question_families:
        return 0.0

    shared = jd_families & question_families
    if shared:
        return 40.0 * len(shared)

    return -70.0


def _question_blob(question_text: str, topics: list[str] | set[str]) -> str:
    topic_list = sorted(set(topics))
    return f"{question_text} {' '.join(topic_list)}".strip()


def compute_relevance_score(
    question_text: str,
    topics: list[str] | set[str],
    relevance_text: str,
    times_seen: int,
) -> float:
    item = AggregatedQuestion(question=question_text, times_seen=times_seen)
    item.topics = set(topics)
    return _rank_score(item, relevance_text)


def select_bank_questions(pool: list[dict], limit: int) -> list[dict]:
    """Return the top-ranked bank questions, up to limit."""
    if limit <= 0:
        return []
    return list(pool[:limit])


def select_diverse_ranked(
    ranked: list[tuple[float, object]],
    *,
    limit: int,
    question_text,
    pool_multiplier: int = 3,
    score_band: float = 25.0,
) -> list[object]:
    """Pick practice questions with score-aware randomness and near-duplicate avoidance."""
    import random

    if limit <= 0 or not ranked:
        return []

    top_score = ranked[0][0]
    score_floor = top_score - score_band
    pool_size = min(len(ranked), max(limit * pool_multiplier, limit))
    pool = [item for score, item in ranked[:pool_size] if score >= score_floor]
    random.shuffle(pool)

    selected: list[object] = []
    selected_texts: list[str] = []
    for item in pool:
        if len(selected) >= limit:
            break
        text = str(question_text(item) or "").strip()
        if not text:
            continue
        if any(questions_are_similar(text, existing) for existing in selected_texts):
            continue
        selected.append(item)
        selected_texts.append(text)

    if len(selected) < limit:
        for _score, item in ranked:
            if len(selected) >= limit:
                break
            if item in selected:
                continue
            text = str(question_text(item) or "").strip()
            if not text:
                continue
            if any(questions_are_similar(text, existing) for existing in selected_texts):
                continue
            selected.append(item)
            selected_texts.append(text)

    return selected[:limit]


def canonical_bank_question(question: str, pool: list[dict]) -> str | None:
    for item in pool:
        bank_question = str(item.get("question", "")).strip()
        if bank_question and questions_are_similar(question, bank_question):
            return bank_question
    return None


def restrict_to_bank_questions(questions: list, pool: list[dict]) -> list:
    """Keep only predictions that match a saved bank question (exact canonical text)."""
    kept = []
    for item in questions:
        question = str(getattr(item, "question", "")).strip()
        if not question:
            continue
        canonical = canonical_bank_question(question, pool)
        if not canonical:
            continue
        if hasattr(item, "model_copy"):
            item = item.model_copy(
                update={
                    "question": canonical,
                    "source": "past_interview",
                }
            )
        kept.append(item)
    return dedupe_predicted_questions(kept)


def _rank_and_limit(
    aggregated: dict[str, AggregatedQuestion],
    relevance_text: str,
    limit: int,
) -> list[dict]:
    if not aggregated or limit <= 0:
        return []

    ranked = sorted(
        aggregated.values(),
        key=lambda item: (
            _rank_score(item, relevance_text),
            item.times_seen,
            len(item.question),
        ),
        reverse=True,
    )

    return [_to_prompt_item(item) for item in ranked[:limit]]


def _rank_score(item: AggregatedQuestion, relevance_text: str) -> float:
    question_blob = _question_blob(item.question, item.topics)
    overlap = _keyword_overlap(question_blob, relevance_text)
    overlap_score = overlap * 15.0
    frequency_score = min(item.times_seen * 3, 15)
    stack_score = _stack_alignment_score(question_blob, relevance_text)
    return overlap_score + frequency_score + stack_score


def _keyword_overlap(left: str, right: str) -> int:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens)


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text.lower())
        if token not in _STOPWORDS
    }


def _to_prompt_item(item: AggregatedQuestion) -> dict:
    payload = {
        "question": item.question,
        "times_seen": item.times_seen,
    }
    if item.avg_score is not None:
        payload["avg_score"] = item.avg_score
    if item.topics:
        payload["topics"] = sorted(item.topics)[:5]
    return payload


def _canonical_token(token: str) -> str:
    aliases = {
        "caching": "cache",
        "cached": "cache",
        "databases": "database",
        "kubernetes": "k8s",
        "microservices": "microservice",
        "behavioural": "behavioral",
        "behaviors": "behavior",
        "programming": "program",
        "engineers": "engineer",
        "experiences": "experience",
        "questions": "question",
    }
    return aliases.get(token, token)


def _tokenize_canonical(text: str) -> set[str]:
    return {_canonical_token(token) for token in _tokenize(text)}


_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "intro",
        re.compile(
            r"(tell me about yourself|introduce yourself|walk me through your (background|career|experience)|two minute intro|brief intro)",
            re.IGNORECASE,
        ),
    ),
    (
        "motivation",
        re.compile(
            r"(why (do you want|are you interested)|what (attracted|draws) you|why this (role|company|position|job))",
            re.IGNORECASE,
        ),
    ),
    (
        "salary",
        re.compile(r"(salary|compensation|pay (range|expectation)|expected (salary|comp))", re.IGNORECASE),
    ),
    (
        "availability",
        re.compile(r"(start date|when can you start|notice period|availability|timeline to join)", re.IGNORECASE),
    ),
    (
        "weakness",
        re.compile(r"(greatest weakness|area(s)? (for )?improvement|what would you improve)", re.IGNORECASE),
    ),
    (
        "conflict",
        re.compile(
            r"(conflict|disagree|difficult (coworker|teammate|stakeholder|situation))",
            re.IGNORECASE,
        ),
    ),
]


def _question_intents(text: str) -> set[str]:
    intents: set[str] = set()
    for name, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            intents.add(name)
    return intents


def questions_are_similar(left: str, right: str, threshold: float = 0.5) -> bool:
    """Return True when two questions likely ask the same thing."""
    left_norm = normalize_question(left)
    right_norm = normalize_question(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    if _question_intents(left) & _question_intents(right):
        return True

    shorter, longer = (
        (left_norm, right_norm) if len(left_norm) <= len(right_norm) else (right_norm, left_norm)
    )
    if len(shorter) >= 15 and shorter in longer:
        return True

    left_tokens = _tokenize_canonical(left)
    right_tokens = _tokenize_canonical(right)
    if not left_tokens or not right_tokens:
        return False

    intersection = len(left_tokens & right_tokens)
    if intersection < 2:
        return False

    smaller = min(len(left_tokens), len(right_tokens))
    union = len(left_tokens | right_tokens)
    if intersection / smaller >= threshold:
        return True
    return union > 0 and intersection / union >= threshold


def dedupe_predicted_questions(questions: list) -> list:
    """Drop near-duplicate predicted questions, preserving first occurrence."""
    kept = []
    for item in questions:
        question = str(getattr(item, "question", "")).strip()
        if not question:
            continue
        if any(questions_are_similar(question, existing.question) for existing in kept):
            continue
        kept.append(item)
    return kept
