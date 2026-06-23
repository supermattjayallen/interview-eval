import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import TeamBasicAuthMiddleware, team_auth_enabled
from app.interview_steps import INTERVIEW_STEP_LABELS, InterviewStep
from app.models import (
    AnalysisJobResponse,
    BatchAnalysisRequest,
    BatchAnalysisResponse,
    InterviewAnalysisRequest,
    InterviewAnalysisResult,
    InterviewPrepRequest,
    InterviewPrepResult,
    SaveJobDescriptionRequest,
    SavedJobDescription,
)
from app.services.interview_prep import InterviewPrepError, prepare_for_interview
from app.services.job_store import job_store
from app.services.pipeline import get_batch, get_job, run_analysis_sync, start_analysis, start_batch_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Interview Evaluation Service",
    description="Extract interview questions, evaluate candidate answers, and provide actionable feedback from recording links.",
    version="0.1.0",
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
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/interview-steps")
def list_interview_steps() -> list[dict[str, str]]:
    return [
        {"value": step.value, "label": INTERVIEW_STEP_LABELS[step]}
        for step in InterviewStep
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
