import hashlib
import re
from urllib.parse import parse_qs, unquote, urlparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "usp",
    "si",
    "feature",
    "t",
    "start",
    "ab_channel",
}


def normalize_recording_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return "url:empty"

    parsed = urlparse(cleaned)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"

    if "drive.google.com" in host or "docs.google.com" in host:
        file_id = _extract_google_drive_id(cleaned)
        if file_id:
            return f"gdrive:{file_id}"

    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
        if video_id:
            return f"youtube:{video_id}"

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        video_id = _extract_youtube_id(path, parsed.query)
        if video_id:
            return f"youtube:{video_id}"

    if "loom.com" in host:
        match = re.search(r"/(?:share|embed)/([a-zA-Z0-9]+)", path)
        if match:
            return f"loom:{match.group(1)}"

    if "dropbox.com" in host:
        dropbox_id = _extract_dropbox_id(path, cleaned)
        if dropbox_id:
            return f"dropbox:{dropbox_id}"

    if "zoom.us" in host:
        zoom_id = _extract_zoom_id(path)
        if zoom_id:
            return f"zoom:{zoom_id}"

    if "sharepoint.com" in host or "1drv.ms" in host or "onedrive.live.com" in host:
        onedrive_id = _extract_onedrive_id(cleaned)
        if onedrive_id:
            return f"onedrive:{onedrive_id}"

    if _is_direct_media_url(path):
        return f"direct:{_canonical_direct_url(host, path)}"

    return f"url:{_hash_canonical_url(parsed)}"


def recording_storage_key(url: str) -> str:
    normalized = normalize_recording_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return digest


def urls_match_recording(left: str, right: str) -> bool:
    return normalize_recording_url(left) == normalize_recording_url(right)


def _extract_google_drive_id(url: str) -> str | None:
    patterns = [
        r"/file/d/([^/?#]+)",
        r"/folders/([^/?#]+)",
        r"[?&]id=([^&#]+)",
        r"/uc\?[^#]*\bid=([^&#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _extract_youtube_id(path: str, query: str) -> str | None:
    query_params = parse_qs(query)
    video_id = query_params.get("v", [None])[0]
    if video_id:
        return video_id

    for prefix in ("/embed/", "/shorts/", "/live/", "/v/"):
        if path.startswith(prefix):
            candidate = path.removeprefix(prefix).split("/")[0]
            if candidate:
                return candidate
    return None


def _extract_dropbox_id(path: str, url: str) -> str | None:
    match = re.search(r"/s(?:cl)?/([^/?#]+)", path)
    if match:
        return match.group(1)

    match = re.search(r"dropbox\.com/s(?:cl)?/([^/?#]+)", url)
    if match:
        return match.group(1)
    return None


def _extract_zoom_id(path: str) -> str | None:
    match = re.search(r"/rec(?:ording)?/(?:share/)?([^/?#]+)", path)
    if match:
        return match.group(1)
    return None


def _extract_onedrive_id(url: str) -> str | None:
    match = re.search(r"resid=([^&#]+)", url, re.IGNORECASE)
    if match:
        return unquote(match.group(1))

    match = re.search(r"/personal/[^/?#]+/([^/?#]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _is_direct_media_url(path: str) -> bool:
    return bool(re.search(r"\.(mp3|mp4|m4a|wav|webm|ogg|mov|mkv)$", path.lower()))


def _canonical_direct_url(host: str, path: str) -> str:
    return f"{host}{path.lower()}"


def _hash_canonical_url(parsed) -> str:
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    query_params = parse_qs(parsed.query)

    filtered = []
    for key in sorted(query_params):
        if key in TRACKING_PARAMS:
            continue
        for value in query_params[key]:
            filtered.append(f"{key}={value}")

    canonical = f"{host}{path}"
    if filtered:
        canonical += "?" + "&".join(filtered)

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
