import logging
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp
from yt_dlp.utils import DownloadError

from app.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_DIRECT_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".mov", ".mkv"}


class RecordingFetchError(Exception):
    pass


def _ensure_temp_dir() -> Path:
    temp_dir = Path(settings.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _is_direct_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in SUPPORTED_DIRECT_EXTENSIONS)


def _download_direct(url: str, destination: Path) -> Path:
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    return destination


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _ytdlp_option_sets(output_template: str) -> list[dict]:
    base_opts = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    # Prefer native audio download to avoid requiring ffmpeg for conversion.
    option_sets = [
        {
            **base_opts,
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        }
    ]

    if _ffmpeg_available():
        option_sets.append(
            {
                **base_opts,
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "0",
                    }
                ],
            }
        )

    return option_sets


def _download_with_ytdlp(url: str, job_dir: Path) -> Path:
    output_template = str(job_dir / "recording.%(ext)s")
    last_error = "Unknown yt-dlp error"

    for ydl_opts in _ytdlp_option_sets(output_template):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            break
        except DownloadError as exc:
            last_error = str(exc)
        except Exception as exc:
            last_error = str(exc)
    else:
        if "ffmpeg" in last_error.lower() or "ffprobe" in last_error.lower():
            raise RecordingFetchError(
                "ffmpeg is required to process this recording format. "
                "Install ffmpeg and ensure it is on your PATH."
            )
        raise RecordingFetchError(f"Failed to download recording: {last_error}")

    audio_files = list(job_dir.glob("recording.*"))
    if not audio_files:
        raise RecordingFetchError("Download completed but no audio file was produced")

    return audio_files[0]


def fetch_recording(url: str) -> tuple[Path, Path]:
    """
    Download a recording from a URL and return (audio_path, job_dir).
    The caller is responsible for cleaning up job_dir when done.
    """
    job_id = uuid.uuid4().hex
    job_dir = _ensure_temp_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        if _is_direct_media_url(url):
            ext = Path(urlparse(url).path).suffix or ".mp3"
            destination = job_dir / f"recording{ext}"
            audio_path = _download_direct(url, destination)
        else:
            audio_path = _download_with_ytdlp(url, job_dir)

        logger.info("Downloaded recording to %s", audio_path)
        return audio_path, job_dir
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def cleanup_job_dir(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)
