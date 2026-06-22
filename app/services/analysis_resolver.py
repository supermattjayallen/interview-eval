import logging
from typing import Optional

from app.models import AnalysisJobStatus, InterviewAnalysisRequest, InterviewAnalysisResult
from app.services.analyzer import AnalysisError, analyze_interview, reevaluate_interview
from app.services.job_store import job_store
from app.services.recording_fetcher import RecordingFetchError, cleanup_job_dir, fetch_recording
from app.services.result_store import ResultStoreError, result_store
from app.services.transcriber import TranscriptionError, transcribe_audio

logger = logging.getLogger(__name__)

_REEVAL_FIELDS = ("evaluation_criteria",)


def _normalize_context_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "|".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def evaluation_criteria_changed(saved_request: dict, request: InterviewAnalysisRequest) -> bool:
    """Only evaluation criteria changes warrant re-scoring; role title/description do not."""
    new_values = request.model_dump(mode="json")
    for field in _REEVAL_FIELDS:
        old_value = _normalize_context_value(saved_request.get(field))
        new_value = _normalize_context_value(new_values.get(field))
        if new_value and new_value != old_value:
            return True
    return False


class ResolvedAnalysis:
    def __init__(
        self,
        result: InterviewAnalysisResult,
        status: AnalysisJobStatus,
        message: str,
    ) -> None:
        self.result = result
        self.status = status
        self.message = message


def resolve_analysis(
    request: InterviewAnalysisRequest,
    on_status=None,
) -> ResolvedAnalysis:
    """
    Return cached, re-evaluated, or freshly analyzed results for a recording.
    on_status optional callback(status, message) for progress updates.
    """
    def notify(status: AnalysisJobStatus, message: str) -> None:
        if on_status:
            on_status(status, message)

    if not request.force_refresh:
        try:
            payload = result_store.load_payload(request)
        except ResultStoreError as exc:
            logger.warning("Could not load saved result: %s", exc)
            payload = None

        if payload:
            cached = InterviewAnalysisResult.model_validate(payload["result"])
            cached.from_saved_data = True
            cached.saved_at = payload.get("saved_at")
            cached.storage_source = payload.get("_source", "local")
            saved_request = payload.get("request", {})

            if evaluation_criteria_changed(saved_request, request):
                notify(AnalysisJobStatus.ANALYZING, "Re-evaluating with updated evaluation criteria...")
                result = reevaluate_interview(request, cached)
                result_store.save(request, result)
                if request.role_title and request.role_description:
                    job_store.save(request.role_title, request.role_description)
                return ResolvedAnalysis(
                    result,
                    AnalysisJobStatus.COMPLETED,
                    "Re-evaluated with updated evaluation criteria (Q&A reused, scores refreshed)",
                )

            if cached.evaluation_skipped and not request.skip_evaluation:
                notify(AnalysisJobStatus.ANALYZING, "Evaluating previously extracted answers...")
                result = reevaluate_interview(request, cached)
                result_store.save(request, result)
                if request.role_title and request.role_description:
                    job_store.save(request.role_title, request.role_description)
                return ResolvedAnalysis(
                    result,
                    AnalysisJobStatus.COMPLETED,
                    "Evaluated answers for previously saved question bank entry",
                )

            if request.role_title and request.role_description:
                job_store.save(request.role_title, request.role_description)

            source = cached.storage_source or "saved storage"
            return ResolvedAnalysis(
                cached,
                AnalysisJobStatus.CACHED,
                f"Loaded saved Q&A from {source}",
            )

    job_dir = None
    try:
        notify(AnalysisJobStatus.DOWNLOADING, "Downloading recording...")
        audio_path, job_dir = fetch_recording(str(request.recording_url))

        notify(AnalysisJobStatus.TRANSCRIBING, "Transcribing audio...")
        transcript = transcribe_audio(audio_path, language=request.language)

        notify(
            AnalysisJobStatus.ANALYZING,
            "Extracting questions and answers (skipping evaluation)..."
            if request.skip_evaluation
            else "Extracting all questions and full answers...",
        )
        result = analyze_interview(request, transcript)
        locations = result_store.save(request, result)

        if request.role_title and request.role_description:
            job_store.save(request.role_title, request.role_description)

        save_message = "Question bank saved (evaluation skipped)" if result.evaluation_skipped else "Analysis complete"
        if locations:
            save_message += f" and saved to {', '.join(locations)}"

        return ResolvedAnalysis(result, AnalysisJobStatus.COMPLETED, save_message)
    except (RecordingFetchError, TranscriptionError, AnalysisError):
        raise
    finally:
        if job_dir:
            cleanup_job_dir(job_dir)
