from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.interview_steps import InterviewStep


class AnswerQuality(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    PARTIAL = "partial"
    WEAK = "weak"
    INCORRECT = "incorrect"
    NOT_ANSWERED = "not_answered"


class InterviewAnalysisRequest(BaseModel):
    recording_url: HttpUrl = Field(..., description="URL to the interview recording (video or audio)")
    role_title: Optional[str] = Field(None, description="Job title being interviewed for")
    role_description: Optional[str] = Field(None, description="Job description or required skills")
    interview_step: Optional[InterviewStep] = Field(
        None,
        description="Interview round/step (e.g. recruiter screen, technical, system design)",
    )
    evaluation_criteria: Optional[list[str]] = Field(
        None,
        description="Custom criteria to evaluate answers against (e.g. 'system design depth', 'communication clarity')",
    )
    interviewer_label: str = Field("Interviewer", description="Label for the interviewer in the transcript")
    candidate_label: str = Field("Candidate", description="Label for the candidate in the transcript")
    first_speaker: Literal["interviewer", "candidate"] = Field(
        "interviewer",
        description="Who speaks first at the start of the recording",
    )
    language: str = Field("en", description="Primary language of the interview")
    force_refresh: bool = Field(
        False,
        description="Re-run analysis even if saved results exist for this recording",
    )
    skip_evaluation: bool = Field(
        False,
        description="Extract Q&A only; skip scoring, ideal answers, and feedback",
    )


class QuestionAnswerPair(BaseModel):
    question: str
    question_timestamp: Optional[str] = None
    answer: str = Field(..., description="Full candidate answer extracted from the transcript")
    answer_timestamp: Optional[str] = None
    ideal_answer: str = Field(
        default="",
        description="Complete suggested better answer for this question",
    )
    quality: AnswerQuality
    score: int = Field(..., ge=0, le=10, description="Score from 0-10")
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    ideal_answer_points: list[str] = Field(default_factory=list)


class InterviewFeedback(BaseModel):
    candidate_feedback: list[str] = Field(
        default_factory=list,
        description="Actionable feedback for the candidate to improve future interviews",
    )
    overall_recommendation: str = Field(
        ...,
        description="Hire / no-hire / needs follow-up recommendation with rationale",
    )


class InterviewAnalysisResult(BaseModel):
    recording_url: str
    role_title: Optional[str] = None
    interview_step: Optional[InterviewStep] = None
    interview_step_inferred: bool = Field(
        default=False,
        description="True when interview_step was auto-detected because the user did not select one",
    )
    transcript_summary: str
    total_questions: int
    average_score: float
    qa_pairs: list[QuestionAnswerPair]
    feedback: InterviewFeedback
    topics_covered: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    from_saved_data: bool = Field(
        default=False,
        description="True when this result was loaded from saved storage",
    )
    saved_at: Optional[str] = Field(None, description="When this result was saved")
    storage_source: Optional[str] = Field(
        None,
        description="Where the result was loaded/saved from, e.g. local or google_drive",
    )
    reevaluated_with_new_context: bool = Field(
        default=False,
        description="True when scores/feedback were refreshed using updated evaluation criteria",
    )
    evaluation_skipped: bool = Field(
        default=False,
        description="True when answer scoring and feedback were skipped (question bank mode)",
    )


class AnalysisJobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


class AnalysisJobResponse(BaseModel):
    job_id: str
    status: AnalysisJobStatus
    message: Optional[str] = None
    result: Optional[InterviewAnalysisResult] = None


class BatchAnalysisRequest(BaseModel):
    recording_urls: list[HttpUrl] = Field(..., min_length=1, max_length=20)
    role_title: Optional[str] = None
    role_description: Optional[str] = None
    interview_step: Optional[InterviewStep] = None
    evaluation_criteria: Optional[list[str]] = None
    interviewer_label: str = "Interviewer"
    candidate_label: str = "Candidate"
    first_speaker: Literal["interviewer", "candidate"] = "interviewer"
    language: str = "en"
    force_refresh: bool = False
    skip_evaluation: bool = Field(
        True,
        description="Skip answer scoring for batch runs to reduce cost when building a question bank",
    )

    @field_validator("recording_urls")
    @classmethod
    def validate_unique_urls(cls, urls: list[HttpUrl]) -> list[HttpUrl]:
        normalized = [str(url).strip() for url in urls]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate recording URLs are not allowed in a batch")
        return urls


class BatchAnalysisItem(BaseModel):
    recording_url: str
    status: AnalysisJobStatus
    message: Optional[str] = None
    result: Optional[InterviewAnalysisResult] = None


class BatchAnalysisResponse(BaseModel):
    batch_id: str
    status: AnalysisJobStatus
    message: Optional[str] = None
    total_count: int
    completed_count: int = 0
    failed_count: int = 0
    cached_count: int = 0
    current_index: int = 0
    items: list[BatchAnalysisItem]


class SavedJobDescription(BaseModel):
    job_id: str
    role_title: str
    role_description: str
    company: Optional[str] = None
    saved_at: str


class SaveJobDescriptionRequest(BaseModel):
    role_title: str = Field(..., min_length=1)
    role_description: str = Field(..., min_length=1)
    company: Optional[str] = None


class InterviewPrepRequest(BaseModel):
    role_title: str = Field(..., min_length=1)
    role_description: str = Field(..., min_length=1)
    interview_step: InterviewStep = Field(
        ...,
        description="The interview round you are preparing for",
    )
    company: Optional[str] = None
    job_id: Optional[str] = Field(None, description="Use a previously saved job description")
    save_job_description: bool = Field(
        True,
        description="Save this job description for future prep sessions",
    )


class PredictedQuestion(BaseModel):
    question: str
    category: str = Field(..., description="e.g. technical, behavioral, system_design, role_specific")
    why_likely: str
    source: Literal["past_interview", "job_description", "both"] = "both"
    based_on_role: Optional[str] = None
    preparation_tips: list[str] = Field(default_factory=list)
    strong_answer_outline: str = ""


class InterviewPrepResult(BaseModel):
    role_title: str
    role_description: str
    interview_step: InterviewStep
    company: Optional[str] = None
    saved_interviews_used: int
    matching_step_interviews_used: int
    past_questions_reviewed: int
    unique_past_questions_used: int = Field(
        0,
        description="Unique deduplicated past questions sent to the prediction model",
    )
    prep_summary: str
    predicted_questions: list[PredictedQuestion]
    focus_areas: list[str] = Field(default_factory=list)
    saved_job_id: Optional[str] = None
