import json
import logging

from openai import OpenAI

from app.config import settings
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep
from app.models import InterviewPrepRequest, InterviewPrepResult, PredictedQuestion
from app.services.job_store import job_store
from app.services.prep_retrieval import build_prep_bank_selection
from app.services.result_store import result_store

logger = logging.getLogger(__name__)

PREP_SYSTEM_PROMPT = """You help candidates prepare for a specific interview round.

Interview rounds differ a lot even for the same job:
- recruiter_screen: background, motivation, salary, availability
- hiring_manager: scope, team fit, experience depth, leadership
- technical: depth in stack, troubleshooting, architecture basics
- coding: algorithms, data structures, live coding
- system_design: scalability, trade-offs, component design
- behavioral: STAR stories, conflict, teamwork, ownership
- culture_fit: values, collaboration style, communication
- panel: mixed stakeholders, broader scope
- final: executive summary, negotiation, closing concerns

Your job:
- Predict questions for the TARGET interview step only
- Prioritize questions from past interviews with the SAME step
- Use other steps only as weak supplementary context, not as primary source
- Generate additional step-appropriate questions from the job description
- When times_seen is present, treat higher values as more common real questions
- Return valid JSON only"""


class InterviewPrepError(Exception):
    pass


def prepare_for_interview(request: InterviewPrepRequest) -> InterviewPrepResult:
    if not settings.openai_api_key:
        raise InterviewPrepError("OPENAI_API_KEY is not configured")

    role_title = request.role_title.strip()
    role_description = request.role_description.strip()
    company = request.company.strip() if request.company else None
    interview_step = request.interview_step

    if request.job_id:
        saved_job = job_store.get(request.job_id)
        if not saved_job:
            raise InterviewPrepError(f"Saved job description not found: {request.job_id}")
        role_title = saved_job.role_title
        role_description = saved_job.role_description
        company = saved_job.company

    saved_job_id = None
    if request.save_job_description:
        saved_job = job_store.save(role_title, role_description, company=company)
        saved_job_id = saved_job.job_id

    saved_payloads = result_store.list_all_saved()
    bank = build_prep_bank_selection(
        saved_payloads,
        interview_step,
        role_title=role_title,
        role_description=role_description,
    )

    step_label = INTERVIEW_STEP_LABELS[interview_step]
    client = OpenAI(api_key=settings.openai_api_key)
    user_prompt = f"""Prepare interview questions for this upcoming interview round.

Target role title: {role_title}
Target company: {company or "Not specified"}
Target interview step: {interview_step.value}
Target interview step label: {step_label}
Target job description:
{role_description}

Past question bank stats:
- Saved recordings scanned: {bank.saved_interviews_scanned}
- Same-step recordings: {bank.matching_step_interviews}
- Total past questions in bank: {bank.total_past_questions_in_bank}
- Unique same-step questions in bank: {bank.unique_matching_questions_in_bank}
- Unique other-step questions in bank: {bank.unique_other_questions_in_bank}
- Unique questions sent below: {bank.unique_past_questions_used}

Common topics from same-step interviews:
{json.dumps(bank.matching_topics, indent=2) if bank.matching_topics else "[]"}

Top past questions from SAME step (deduplicated, ranked by frequency and JD relevance):
{json.dumps(bank.matching_questions_for_prompt, indent=2) if bank.matching_questions_for_prompt else "[]"}

Supplementary past questions from OTHER steps (deduplicated sample):
{json.dumps(bank.other_questions_for_prompt, indent=2) if bank.other_questions_for_prompt else "[]"}

Return JSON:
{{
  "prep_summary": "2-4 sentences focused on what to expect in this specific round",
  "focus_areas": ["area1", "area2"],
  "predicted_questions": [
    {{
      "question": "likely interview question for this step",
      "category": "technical|behavioral|system_design|role_specific|experience|culture|logistics",
      "why_likely": "why this question fits this interview step",
      "source": "past_interview|job_description|both",
      "based_on_role": "role title from a past interview if relevant, else null",
      "preparation_tips": ["tip1", "tip2"],
      "strong_answer_outline": "how to structure a strong answer for this step"
    }}
  ]
}}

Rules:
- Questions must fit the target step: {step_label}
- Prefer past questions from the same step, especially those with higher times_seen
- Do not include questions typical of other rounds unless they also fit this step
- Aim for 10 to 18 questions when enough same-step data exists, otherwise at least 6"""

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": PREP_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except Exception as exc:
        raise InterviewPrepError(f"Interview prep failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise InterviewPrepError("Interview prep returned empty response")

    try:
        data = json.loads(content)
        predicted = [PredictedQuestion.model_validate(item) for item in data["predicted_questions"]]
        result = InterviewPrepResult(
            role_title=role_title,
            role_description=role_description,
            interview_step=interview_step,
            company=company,
            saved_interviews_used=bank.saved_interviews_scanned,
            matching_step_interviews_used=bank.matching_step_interviews,
            past_questions_reviewed=bank.total_past_questions_in_bank,
            unique_past_questions_used=bank.unique_past_questions_used,
            prep_summary=data["prep_summary"],
            predicted_questions=predicted,
            focus_areas=data.get("focus_areas", []),
            saved_job_id=saved_job_id,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise InterviewPrepError(f"Failed to parse interview prep result: {exc}") from exc

    logger.info(
        "Prepared %d questions for step %s using %d/%d unique past questions",
        len(result.predicted_questions),
        interview_step.value,
        result.unique_past_questions_used,
        bank.total_past_questions_in_bank,
    )
    return result
