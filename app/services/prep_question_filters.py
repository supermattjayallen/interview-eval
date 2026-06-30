import re

from app.services.qa_extractor import is_substantive_question

_PREP_EXCLUDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow many (people|persons|engineers|developers|members|folks|employees|teammates)\b", re.I),
    re.compile(r"\b(team size|size of (your |the )?team|how big was (your |the )?team)\b", re.I),
    re.compile(r"\bhow (large|big) (was|is) (your |the )?team\b", re.I),
    re.compile(r"\bcan you (hear|see) (me|my|us)\b", re.I),
    re.compile(r"\b(audio|video|screen share|connection|muted|unmute)\b", re.I),
    re.compile(r"\bwho (else )?did you (speak|talk|interview) with\b", re.I),
    re.compile(r"\bwhat (time zone|timezone|hours) (do you|are you)\b", re.I),
    re.compile(r"\bwhen can you start\b", re.I),
    re.compile(r"\bwhat is your (notice period|salary|compensation|expected pay)\b", re.I),
    re.compile(r"\bare you (authorized|eligible) to work\b", re.I),
    re.compile(r"\bwhat(?:'s| is) your availability\b", re.I),
    re.compile(r"\bwhere are you (based|located|from)\b", re.I),
)


def is_prep_worthy_question(question: str) -> bool:
    """Stricter than substantive — suitable for interview practice, not logistics or clarifiers."""
    text = str(question or "").strip()
    if not text or not is_substantive_question(text):
        return False

    lowered = text.lower()
    for pattern in _PREP_EXCLUDE_PATTERNS:
        if pattern.search(lowered):
            return False

    if re.match(r"^how many \w+", lowered) and len(lowered.split()) <= 10:
        return False

    return True
