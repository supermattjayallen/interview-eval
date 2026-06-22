import json
import logging

from openai import OpenAI

from app.config import settings
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep

logger = logging.getLogger(__name__)

INFER_SYSTEM_PROMPT = """You classify which interview round a completed interview belongs to.

Choose exactly one step based on the questions asked:
- recruiter_screen
- hiring_manager
- technical
- coding
- system_design
- behavioral
- culture_fit
- panel
- final
- other

Return valid JSON only."""


def infer_interview_step(questions: list[str], role_title: str | None = None) -> InterviewStep:
    if not settings.openai_api_key or not questions:
        return InterviewStep.OTHER

    client = OpenAI(api_key=settings.openai_api_key)
    step_options = "\n".join(f"- {step.value}: {INTERVIEW_STEP_LABELS[step]}" for step in InterviewStep)

    user_prompt = f"""Classify this interview based on the questions that were asked.

Role title: {role_title or "Unknown"}

Questions asked:
{json.dumps(questions[:30], indent=2)}

Valid steps:
{step_options}

Return JSON:
{{
  "interview_step": "one of the step values above",
  "confidence": "high|medium|low",
  "reason": "brief reason"
}}"""

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": INFER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content
        if not content:
            return InterviewStep.OTHER

        data = json.loads(content)
        step = InterviewStep(data["interview_step"])
        logger.info(
            "Inferred interview step %s (%s confidence): %s",
            step.value,
            data.get("confidence", "unknown"),
            data.get("reason", ""),
        )
        return step
    except Exception as exc:
        logger.warning("Could not infer interview step: %s", exc)
        return InterviewStep.OTHER
