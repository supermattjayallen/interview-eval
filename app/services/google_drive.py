import io
import json
import logging
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
MIME_TYPE = "application/json"


class GoogleDriveStorageError(Exception):
    pass


class GoogleDriveStorage:
    def __init__(self, credentials_path: str, folder_id: str) -> None:
        self.folder_id = folder_id
        self.credentials_path = Path(credentials_path)
        if not self.credentials_path.exists():
            raise GoogleDriveStorageError(
                f"Google Drive credentials file not found: {credentials_path}"
            )
        self._service = None

    @property
    def service(self):
        if self._service is None:
            credentials = service_account.Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=SCOPES,
            )
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    def upload_json(self, filename: str, payload: dict) -> str:
        content = json.dumps(payload, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=MIME_TYPE, resumable=False)
        existing_file_id = self._find_file_id(filename)

        if existing_file_id:
            updated = (
                self.service.files()
                .update(fileId=existing_file_id, media_body=media, fields="id")
                .execute()
            )
            return updated["id"]

        created = (
            self.service.files()
            .create(
                body={
                    "name": filename,
                    "parents": [self.folder_id],
                    "mimeType": MIME_TYPE,
                },
                media_body=media,
                fields="id",
            )
            .execute()
        )
        return created["id"]

    def download_json(self, filename: str) -> dict | None:
        file_id = self._find_file_id(filename)
        if not file_id:
            return None
        return self._download_file_by_id(file_id)

    def list_json_files(self) -> list[dict]:
        query = (
            f"'{self.folder_id}' in parents and trashed = false "
            "and mimeType = 'application/json'"
        )
        response = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id, name)", pageSize=100)
            .execute()
        )
        payloads: list[dict] = []
        for item in response.get("files", []):
            payload = self._download_file_by_id(item["id"])
            if payload:
                payloads.append(payload)
        return payloads

    def _download_file_by_id(self, file_id: str) -> dict | None:
        request = self.service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        buffer.seek(0)
        return json.loads(buffer.read().decode("utf-8"))

    def _find_file_id(self, filename: str) -> str | None:
        query = (
            f"name = '{filename}' and '{self.folder_id}' in parents and trashed = false"
        )
        response = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = response.get("files", [])
        if not files:
            return None
        return files[0]["id"]
