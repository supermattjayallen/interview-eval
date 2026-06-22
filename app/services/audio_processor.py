import logging
import math
import shutil
import subprocess
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_MAX_BYTES = 24 * 1024 * 1024
WAV_HEADER_BYTES = 44
CHUNK_SECONDS = 600


class AudioProcessingError(Exception):
    pass


def prepare_audio_files(audio_path: Path) -> list[Path]:
    """Return one or more audio files, each under the Whisper upload limit."""
    if audio_path.stat().st_size <= WHISPER_MAX_BYTES:
        return [audio_path]

    job_dir = audio_path.parent
    compressed_path = job_dir / f"{audio_path.stem}_compressed.m4a"

    for bitrate in (96000, 64000, 48000, 32000):
        try:
            _compress_audio(audio_path, compressed_path, bitrate=bitrate)
        except AudioProcessingError:
            continue

        size = compressed_path.stat().st_size
        logger.info("Compressed audio to %d bytes at %d bps", size, bitrate)
        if size <= WHISPER_MAX_BYTES:
            return [compressed_path]

    wav_path = job_dir / f"{audio_path.stem}_converted.wav"
    _convert_to_wav(compressed_path if compressed_path.exists() else audio_path, wav_path)
    chunks = _split_wav(wav_path, job_dir)
    if not chunks:
        raise AudioProcessingError(
            "Recording is too large to transcribe even after compression."
        )
    return chunks


def _compress_audio(source: Path, destination: Path, bitrate: int) -> None:
    if shutil.which("ffmpeg"):
        _compress_with_ffmpeg(source, destination, bitrate)
        return

    if Path("/usr/bin/afconvert").exists():
        _compress_with_afconvert(source, destination, bitrate)
        return

    raise AudioProcessingError(
        "Recording exceeds Whisper's 25 MB limit and no audio compression tool is available."
    )


def _compress_with_ffmpeg(source: Path, destination: Path, bitrate: int) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-b:a",
        str(bitrate),
        str(destination),
    ]
    _run_command(cmd, "ffmpeg compression failed")


def _compress_with_afconvert(source: Path, destination: Path, bitrate: int) -> None:
    cmd = [
        "afconvert",
        str(source),
        str(destination),
        "-d",
        "aac",
        "-f",
        "m4af",
        "-b",
        str(bitrate),
    ]
    _run_command(cmd, "afconvert compression failed")


def _convert_to_wav(source: Path, destination: Path) -> None:
    if shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "16000", str(destination)]
        _run_command(cmd, "ffmpeg wav conversion failed")
        return

    cmd = [
        "afconvert",
        str(source),
        str(destination),
        "-d",
        "LEI16",
        "-f",
        "WAVE",
        "-c",
        "1",
    ]
    _run_command(cmd, "afconvert wav conversion failed")


def _split_wav(wav_path: Path, output_dir: Path) -> list[Path]:
    chunk_paths: list[Path] = []

    with wave.open(str(wav_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()
        bytes_per_frame = channels * sample_width
        max_chunk_frames = max(1, (WHISPER_MAX_BYTES - WAV_HEADER_BYTES) // bytes_per_frame)
        chunk_frames = min(frame_rate * CHUNK_SECONDS, max_chunk_frames)
        total_chunks = math.ceil(total_frames / chunk_frames)

        for index in range(total_chunks):
            wav_file.setpos(index * chunk_frames)
            frames = wav_file.readframes(min(chunk_frames, total_frames - index * chunk_frames))
            if not frames:
                continue

            chunk_path = output_dir / f"{wav_path.stem}_chunk_{index:03d}.wav"
            with wave.open(str(chunk_path), "wb") as chunk_file:
                chunk_file.setnchannels(channels)
                chunk_file.setsampwidth(sample_width)
                chunk_file.setframerate(frame_rate)
                chunk_file.writeframes(frames)

            chunk_size = chunk_path.stat().st_size
            if chunk_size <= WHISPER_MAX_BYTES:
                chunk_paths.append(chunk_path)
                logger.info("Created audio chunk %s (%d bytes)", chunk_path.name, chunk_size)
            else:
                raise AudioProcessingError(
                    "Unable to split recording into Whisper-compatible chunks."
                )

    return chunk_paths


def _run_command(cmd: list[str], error_message: str) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AudioProcessingError(error_message) from exc

    if result.returncode != 0:
        details = result.stderr or result.stdout or error_message
        raise AudioProcessingError(details)
