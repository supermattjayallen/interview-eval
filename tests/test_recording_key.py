from app.services.recording_key import normalize_recording_url, recording_storage_key, urls_match_recording


def test_google_drive_urls_match():
    urls = [
        "https://drive.google.com/file/d/abc123/view?usp=sharing",
        "https://drive.google.com/open?id=abc123",
        "https://drive.google.com/uc?export=download&id=abc123",
        "https://docs.google.com/uc?id=abc123",
    ]
    normalized = {normalize_recording_url(url) for url in urls}
    keys = {recording_storage_key(url) for url in urls}
    assert normalized == {"gdrive:abc123"}
    assert len(keys) == 1
    assert urls_match_recording(urls[0], urls[1])


def test_youtube_urls_match():
    urls = [
        "https://www.youtube.com/watch?v=xyz99",
        "https://youtu.be/xyz99",
        "https://youtube.com/watch?v=xyz99&t=12",
        "https://www.youtube.com/embed/xyz99",
    ]
    assert len({recording_storage_key(url) for url in urls}) == 1


def test_different_recordings_do_not_match():
    left = "https://drive.google.com/file/d/abc123/view"
    right = "https://drive.google.com/file/d/def456/view"
    assert not urls_match_recording(left, right)
