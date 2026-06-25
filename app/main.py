import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import TeamBasicAuthMiddleware, team_auth_enabled
from app.db import database_enabled, init_database
from app.db.question_bank import question_bank_store
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep
from app.prep_categories import PREP_CATEGORY_LABELS, PrepQuestionCategory
from app.models import (
    AnalysisJobResponse,
    BatchAnalysisRequest,
    BatchAnalysisResponse,
    InterviewAnalysisRequest,
    InterviewAnalysisResult,
    InterviewPrepRequest,
    InterviewPrepResult,
    RegenerateIdealAnswerRequest,
    RegenerateIdealAnswerResponse,
    SaveJobDescriptionRequest,
    SavedJobDescription,
)
from app.services.interview_prep import InterviewPrepError, prepare_for_interview
from app.services.job_store import job_store
from app.services.analyzer import AnalysisError, regenerate_ideal_answer
from app.services.pipeline import get_batch, get_job, run_analysis_sync, start_analysis, start_batch_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title="Interview Evaluation Service",
    description="Extract interview questions, evaluate candidate answers, and provide actionable feedback from recording links.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if team_auth_enabled():
    app.add_middleware(TeamBasicAuthMiddleware)
    logging.getLogger(__name__).info("Team login enabled (HTTP Basic Auth)")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str | int]:
    status: dict[str, str | int] = {"status": "ok"}
    if database_enabled():
        try:
            status["postgres_recordings"] = question_bank_store.count_recordings()
            status["postgres"] = "ok"
        except Exception as exc:
            status["postgres"] = "error"
            status["postgres_error"] = str(exc)
    return status


@app.get("/interview-steps")
def list_interview_steps() -> list[dict[str, str]]:
    return [
        {"value": step.value, "label": INTERVIEW_STEP_LABELS[step]}
        for step in InterviewStep
    ]


@app.get("/prep/categories")
def list_prep_categories() -> list[dict[str, str]]:
    return [
        {"value": category.value, "label": PREP_CATEGORY_LABELS[category]}
        for category in PrepQuestionCategory
    ]


@app.post("/analyze", response_model=AnalysisJobResponse)
def analyze_async(request: InterviewAnalysisRequest) -> AnalysisJobResponse:
    """
    Start an async analysis job. Poll GET /analyze/{job_id} for results.
    Recommended for recordings longer than a few minutes.
    """
    return start_analysis(request)


@app.get("/analyze/{job_id}", response_model=AnalysisJobResponse)
def get_analysis_job(job_id: str) -> AnalysisJobResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/analyze/batch", response_model=BatchAnalysisResponse)
def analyze_batch(request: BatchAnalysisRequest) -> BatchAnalysisResponse:
    """Process multiple recordings sequentially, one by one."""
    return start_batch_analysis(request)


@app.get("/analyze/batch/{batch_id}", response_model=BatchAnalysisResponse)
def get_batch_analysis(batch_id: str) -> BatchAnalysisResponse:
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@app.post("/analyze/sync", response_model=InterviewAnalysisResult)
def analyze_sync(request: InterviewAnalysisRequest) -> InterviewAnalysisResult:
    """
    Run analysis synchronously and return the full result.
    May time out for long recordings; prefer /analyze for production use.
    """
    try:
        return run_analysis_sync(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze/regenerate-ideal-answer", response_model=RegenerateIdealAnswerResponse)
def regenerate_ideal_answer_endpoint(
    request: RegenerateIdealAnswerRequest,
) -> RegenerateIdealAnswerResponse:
    """Regenerate the better answer for one question without changing its score."""
    try:
        qa_pair = regenerate_ideal_answer(
            recording_url=str(request.recording_url),
            question_index=request.question_index,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RegenerateIdealAnswerResponse(
        question_index=request.question_index,
        qa_pair=qa_pair,
    )


@app.post("/jobs", response_model=SavedJobDescription)
def save_job_description(request: SaveJobDescriptionRequest) -> SavedJobDescription:
    return job_store.save(
        role_title=request.role_title,
        role_description=request.role_description,
        company=request.company,
    )


@app.get("/jobs", response_model=list[SavedJobDescription])
def list_job_descriptions() -> list[SavedJobDescription]:
    return job_store.list_all()


@app.post("/prepare", response_model=InterviewPrepResult)
def prepare_interview(request: InterviewPrepRequest) -> InterviewPrepResult:
    try:
        return prepare_for_interview(request)
    except InterviewPrepError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
