import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import SavedJobDescription

logger = logging.getLogger(__name__)


class JobStoreError(Exception):
    pass


class JobStore:
    def __init__(self) -> None:
        self.jobs_dir = Path(settings.jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        role_title: str,
        role_description: str,
        company: str | None = None,
        job_id: str | None = None,
    ) -> SavedJobDescription:
        job = SavedJobDescription(
            job_id=job_id or uuid.uuid4().hex,
            role_title=role_title.strip(),
            role_description=role_description.strip(),
            company=company.strip() if company else None,
            saved_at=datetime.now(UTC).isoformat(),
        )
        path = self._path(job.job_id)
        path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Saved job description %s", job.job_id)
        return job

    def get(self, job_id: str) -> SavedJobDescription | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        return SavedJobDescription.model_validate_json(path.read_text(encoding="utf-8"))

    def list_all(self) -> list[SavedJobDescription]:
        jobs: list[SavedJobDescription] = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                jobs.append(SavedJobDescription.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return jobs

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"


job_store = JobStore()
