import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models import InterviewAnalysisRequest, InterviewAnalysisResult
from app.services.google_drive import GoogleDriveStorage, GoogleDriveStorageError
from app.services.recording_key import normalize_recording_url, recording_storage_key

logger = logging.getLogger(__name__)


class ResultStoreError(Exception):
    pass


class ResultStore:
    def __init__(self) -> None:
        self.local_dir = Path(settings.results_dir)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._drive: GoogleDriveStorage | None = None

    @property
    def drive(self) -> GoogleDriveStorage | None:
        if not settings.google_drive_enabled:
            return None
        if self._drive is None:
            self._drive = GoogleDriveStorage(
                credentials_path=settings.google_drive_credentials_path,
                folder_id=settings.google_drive_folder_id,
            )
        return self._drive

    def load(self, request: InterviewAnalysisRequest) -> Optional[InterviewAnalysisResult]:
        payload = self.load_payload(request)
        if not payload:
            return None

        try:
            result = InterviewAnalysisResult.model_validate(payload["result"])
            result.from_saved_data = True
            result.saved_at = payload.get("saved_at")
            result.storage_source = payload.get("_source", "local")
            logger.info("Loaded saved analysis for %s", payload.get("recording_key"))
            return result
        except (KeyError, ValueError) as exc:
            raise ResultStoreError(f"Saved result is invalid: {exc}") from exc

    def load_payload(self, request: InterviewAnalysisRequest) -> Optional[dict]:
        key = recording_storage_key(str(request.recording_url))
        payload = self._load_local(key)
        source = "local"

        if payload is None:
            payload = self._load_local_by_normalized_url(str(request.recording_url))
            if payload:
                source = "local"

        if payload is None and self.drive:
            try:
                payload = self.drive.download_json(self._filename(key))
                source = "google_drive"
                if payload is None:
                    payload = self._load_drive_by_normalized_url(str(request.recording_url))
                    if payload:
                        source = "google_drive"
                if payload:
                    self._save_local(key, payload)
            except GoogleDriveStorageError as exc:
                logger.warning("Google Drive load failed for %s: %s", key, exc)

        if not payload:
            return None

        payload["_source"] = source
        return payload

    def save(self, request: InterviewAnalysisRequest, result: InterviewAnalysisResult) -> list[str]:
        key = recording_storage_key(str(request.recording_url))
        saved_at = datetime.now(UTC).isoformat()
        result.saved_at = saved_at
        result.from_saved_data = False

        payload = {
            "recording_key": key,
            "normalized_recording_id": normalize_recording_url(str(request.recording_url)),
            "recording_url": str(request.recording_url),
            "saved_at": saved_at,
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }

        locations: list[str] = []
        self._save_local(key, payload)
        locations.append("local")

        if self.drive:
            try:
                self.drive.upload_json(self._filename(key), payload)
                locations.append("google_drive")
            except GoogleDriveStorageError as exc:
                logger.warning("Google Drive save failed for %s: %s", key, exc)

        result.storage_source = ",".join(locations)
        logger.info("Saved analysis for %s to %s", key, ", ".join(locations))
        return locations

    def _filename(self, key: str) -> str:
        return f"interview-analysis-{key}.json"

    def _local_path(self, key: str) -> Path:
        return self.local_dir / self._filename(key)

    def _load_local(self, key: str) -> Optional[dict]:
        path = self._local_path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_local_by_normalized_url(self, recording_url: str) -> Optional[dict]:
        target = normalize_recording_url(recording_url)
        for path in self.local_dir.glob("interview-analysis-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if payload.get("normalized_recording_id") == target:
                return payload
        return None

    def _load_drive_by_normalized_url(self, recording_url: str) -> Optional[dict]:
        if not self.drive:
            return None
        target = normalize_recording_url(recording_url)
        for payload in self.drive.list_json_files():
            if payload.get("normalized_recording_id") == target:
                return payload
        return None

    def _save_local(self, key: str, payload: dict) -> None:
        path = self._local_path(key)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_all_saved(self) -> list[dict]:
        payloads: list[dict] = []
        seen_keys: set[str] = set()

        for path in self.local_dir.glob("interview-analysis-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            key = payload.get("recording_key")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            payloads.append(payload)

        if self.drive:
            try:
                for payload in self.drive.list_json_files():
                    key = payload.get("recording_key")
                    if key and key in seen_keys:
                        continue
                    if key:
                        seen_keys.add(key)
                    payloads.append(payload)
            except GoogleDriveStorageError as exc:
                logger.warning("Could not list Google Drive saved results: %s", exc)

        return payloads


result_store = ResultStore()
