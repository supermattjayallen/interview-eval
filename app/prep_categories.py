import re
from enum import Enum


class PrepQuestionCategory(str, Enum):
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    SYSTEM_DESIGN = "system_design"
    ROLE_SPECIFIC = "role_specific"
    EXPERIENCE = "experience"
    CULTURE = "culture"
    LOGISTICS = "logistics"
    CODING = "coding"
    LEADERSHIP = "leadership"
    OTHER = "other"


PREP_CATEGORY_LABELS = {
    PrepQuestionCategory.TECHNICAL: "Technical",
    PrepQuestionCategory.BEHAVIORAL: "Behavioral",
    PrepQuestionCategory.SYSTEM_DESIGN: "System design",
    PrepQuestionCategory.ROLE_SPECIFIC: "Role-specific",
    PrepQuestionCategory.EXPERIENCE: "Experience",
    PrepQuestionCategory.CULTURE: "Culture",
    PrepQuestionCategory.LOGISTICS: "Logistics",
    PrepQuestionCategory.CODING: "Coding",
    PrepQuestionCategory.LEADERSHIP: "Leadership",
    PrepQuestionCategory.OTHER: "Other",
}

_CATEGORY_ALIASES = {
    "role specific": PrepQuestionCategory.ROLE_SPECIFIC,
    "system design": PrepQuestionCategory.SYSTEM_DESIGN,
    "system-design": PrepQuestionCategory.SYSTEM_DESIGN,
    "culture fit": PrepQuestionCategory.CULTURE,
    "logistic": PrepQuestionCategory.LOGISTICS,
}


def normalize_prep_category(value: str | PrepQuestionCategory) -> PrepQuestionCategory:
    if isinstance(value, PrepQuestionCategory):
        return value

    cleaned = re.sub(r"[^\w\s-]", " ", str(value).strip().lower())
    normalized = cleaned.replace("-", " ").replace("  ", " ").strip().replace(" ", "_")
    if normalized in PrepQuestionCategory._value2member_map_:
        return PrepQuestionCategory(normalized)

    spaced = cleaned.replace("_", " ")
    if spaced in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[spaced]

    return PrepQuestionCategory.OTHER


def default_prep_categories() -> list[PrepQuestionCategory]:
    return list(PrepQuestionCategory)
