"""
Source detection logic.
Determines whether input is a topic, website URL, YouTube URL, image upload, or document upload.
"""
import re
from typing import Any, Optional

YOUTUBE_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([\w\-]+)",
    r"(?:https?://)?(?:www\.)?youtu\.be/([\w\-]+)",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w\-]+)",
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([\w\-]+)",
]

URL_PATTERN = re.compile(
    r"https?://[^\s]+"
)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "txt", "docx", "doc", "md"}


def extract_youtube_id(text: str) -> Optional[str]:
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_url(text: str) -> Optional[str]:
    match = URL_PATTERN.search(text)
    if match:
        url = match.group(0).rstrip(".,;)")
        return url
    return None


def is_youtube_url(url: str) -> bool:
    return bool(extract_youtube_id(url))


def detect_file_source_type(filename: str) -> Optional[str]:
    if not filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return "image"
    if ext in ALLOWED_DOCUMENT_EXTENSIONS:
        return "document"
    return None


def detect_source(
    prompt: str,
    filename: Optional[str] = None,
    file_ext: Optional[str] = None,
) -> dict[str, Any]:
    """
    Determine source type from prompt text and optional uploaded file.

    Returns a source info dict:
    {
        "source_type": "topic|website|youtube|image|document",
        "source_url": str | None,
        "source_file_name": str | None,
        "detected_youtube_id": str | None,
    }
    """
    # File upload takes priority
    if filename:
        ext = (file_ext or (filename.rsplit(".", 1)[-1] if "." in filename else "")).lower()
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            return {
                "source_type": "image",
                "source_url": None,
                "source_file_name": filename,
                "detected_youtube_id": None,
            }
        if ext in ALLOWED_DOCUMENT_EXTENSIONS:
            return {
                "source_type": "document",
                "source_url": None,
                "source_file_name": filename,
                "detected_youtube_id": None,
            }

    # Check prompt for YouTube link
    yt_id = extract_youtube_id(prompt)
    if yt_id:
        url = extract_url(prompt) or f"https://www.youtube.com/watch?v={yt_id}"
        return {
            "source_type": "youtube",
            "source_url": url,
            "source_file_name": None,
            "detected_youtube_id": yt_id,
        }

    # Check prompt for generic website URL
    url = extract_url(prompt)
    if url:
        return {
            "source_type": "website",
            "source_url": url,
            "source_file_name": None,
            "detected_youtube_id": None,
        }

    # Plain topic
    return {
        "source_type": "topic",
        "source_url": None,
        "source_file_name": None,
        "detected_youtube_id": None,
    }
