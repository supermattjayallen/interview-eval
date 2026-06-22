import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.models import (
    AnalysisJobResponse,
    AnalysisJobStatus,
    BatchAnalysisItem,
    BatchAnalysisRequest,
    BatchAnalysisResponse,
    InterviewAnalysisRequest,
    InterviewAnalysisResult,
)
from app.services.analysis_resolver import resolve_analysis
from app.services.analyzer import AnalysisError
from app.services.recording_fetcher import RecordingFetchError
from app.services.transcriber import TranscriptionError

logger = logging.getLogger(__name__)

_jobs: dict[str, AnalysisJobResponse] = {}
_batches: dict[str, BatchAnalysisResponse] = {}
_executor = ThreadPoolExecutor(max_workers=2)


def _update_job(job_id: str, status: AnalysisJobStatus, message: str = "", result: Optional[InterviewAnalysisResult] = None) -> None:
    job = _jobs[job_id]
    _jobs[job_id] = AnalysisJobResponse(
        job_id=job_id,
        status=status,
        message=message or job.message,
        result=result or job.result,
    )


def _update_batch(batch_id: str, **updates) -> None:
    batch = _batches[batch_id]
    _batches[batch_id] = batch.model_copy(update=updates)


def _update_batch_item(batch_id: str, index: int, **updates) -> None:
    batch = _batches[batch_id]
    items = list(batch.items)
    items[index] = items[index].model_copy(update=updates)
    _update_batch(batch_id, items=items)


def _process_recording(request: InterviewAnalysisRequest) -> InterviewAnalysisResult:
    resolved = resolve_analysis(request)
    return resolved.result


def _run_pipeline(job_id: str, request: InterviewAnalysisRequest) -> None:
    try:
        def on_status(status: AnalysisJobStatus, message: str) -> None:
            _update_job(job_id, status, message)

        resolved = resolve_analysis(request, on_status=on_status)
        _update_job(job_id, resolved.status, resolved.message, result=resolved.result)
    except RecordingFetchError as exc:
        logger.exception("Recording fetch failed for job %s", job_id)
        _update_job(job_id, AnalysisJobStatus.FAILED, str(exc))
    except TranscriptionError as exc:
        logger.exception("Transcription failed for job %s", job_id)
        _update_job(job_id, AnalysisJobStatus.FAILED, str(exc))
    except AnalysisError as exc:
        logger.exception("Analysis failed for job %s", job_id)
        _update_job(job_id, AnalysisJobStatus.FAILED, str(exc))
    except Exception as exc:
        logger.exception("Unexpected error for job %s", job_id)
        _update_job(job_id, AnalysisJobStatus.FAILED, f"Unexpected error: {exc}")


def _run_batch_pipeline(batch_id: str, request: BatchAnalysisRequest) -> None:
    batch = _batches[batch_id]
    completed = 0
    failed = 0
    cached = 0

    try:
        for index, url in enumerate(request.recording_urls):
            current = index + 1
            total = len(request.recording_urls)
            _update_batch(
                batch_id,
                status=AnalysisJobStatus.DOWNLOADING,
                message=f"Processing recording {current} of {total}...",
                current_index=index,
            )
            _update_batch_item(
                batch_id,
                index,
                status=AnalysisJobStatus.DOWNLOADING,
                message=f"Processing recording {current} of {total}...",
            )

            item_request = InterviewAnalysisRequest(
                recording_url=url,
                role_title=request.role_title,
                role_description=request.role_description,
                interview_step=request.interview_step,
                evaluation_criteria=request.evaluation_criteria,
                interviewer_label=request.interviewer_label,
                candidate_label=request.candidate_label,
                first_speaker=request.first_speaker,
                language=request.language,
                force_refresh=request.force_refresh,
                skip_evaluation=request.skip_evaluation,
            )

            try:
                resolved = resolve_analysis(item_request)
                if resolved.status == AnalysisJobStatus.CACHED:
                    cached += 1
                completed += 1
                _update_batch_item(
                    batch_id,
                    index,
                    status=resolved.status,
                    message=resolved.message,
                    result=resolved.result,
                )
            except (RecordingFetchError, TranscriptionError, AnalysisError) as exc:
                failed += 1
                _update_batch_item(
                    batch_id,
                    index,
                    status=AnalysisJobStatus.FAILED,
                    message=str(exc),
                )
            except Exception as exc:
                failed += 1
                _update_batch_item(
                    batch_id,
                    index,
                    status=AnalysisJobStatus.FAILED,
                    message=f"Unexpected error: {exc}",
                )

        final_status = AnalysisJobStatus.COMPLETED if failed == 0 else AnalysisJobStatus.FAILED
        _update_batch(
            batch_id,
            status=final_status,
            message=f"Batch finished: {completed} completed ({cached} cached), {failed} failed",
            completed_count=completed,
            failed_count=failed,
            cached_count=cached,
        )
    except Exception as exc:
        logger.exception("Batch pipeline failed for %s", batch_id)
        _update_batch(
            batch_id,
            status=AnalysisJobStatus.FAILED,
            message=f"Batch failed: {exc}",
        )


def start_analysis(request: InterviewAnalysisRequest) -> AnalysisJobResponse:
    job_id = uuid.uuid4().hex
    response = AnalysisJobResponse(
        job_id=job_id,
        status=AnalysisJobStatus.PENDING,
        message="Job queued",
    )
    _jobs[job_id] = response
    _executor.submit(_run_pipeline, job_id, request)
    return response


def start_batch_analysis(request: BatchAnalysisRequest) -> BatchAnalysisResponse:
    batch_id = uuid.uuid4().hex
    items = [
        BatchAnalysisItem(recording_url=str(url), status=AnalysisJobStatus.PENDING, message="Queued")
        for url in request.recording_urls
    ]
    response = BatchAnalysisResponse(
        batch_id=batch_id,
        status=AnalysisJobStatus.PENDING,
        message="Batch queued",
        total_count=len(items),
        items=items,
    )
    _batches[batch_id] = response
    _executor.submit(_run_batch_pipeline, batch_id, request)
    return response


def get_job(job_id: str) -> Optional[AnalysisJobResponse]:
    return _jobs.get(job_id)


def get_batch(batch_id: str) -> Optional[BatchAnalysisResponse]:
    return _batches.get(batch_id)


def run_analysis_sync(request: InterviewAnalysisRequest) -> InterviewAnalysisResult:
    return _process_recording(request)
