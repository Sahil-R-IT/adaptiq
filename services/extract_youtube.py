"""
YouTube transcript extraction.
Fetches captions/transcript for a YouTube video to use as quiz source content.

Targets youtube-transcript-api 1.x.
Uses the modern instance-based API:
    api = YouTubeTranscriptApi()
    api.fetch(video_id)
    api.list(video_id)
"""

import re
import time
from typing import Optional, Any, Iterable
from xml.etree.ElementTree import ParseError

MAX_TRANSCRIPT_CHARS = 12000
TRANSCRIPT_FETCH_RETRIES = 2
TRANSCRIPT_FETCH_RETRY_DELAY_SEC = 0.75

YT_TRANSCRIPT_AVAILABLE = False

# Default to None, never Exception.
NoTranscriptFound = None
TranscriptsDisabled = None
VideoUnavailable = None
RequestBlocked = None
IpBlocked = None
YouTubeTranscriptApi = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi

    YT_TRANSCRIPT_AVAILABLE = True

    try:
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
            RequestBlocked,
            IpBlocked,
        )
    except ImportError:
        # Fallback in case package layout changes
        try:
            from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled
        except ImportError:
            NoTranscriptFound = None
            TranscriptsDisabled = None

        VideoUnavailable = None
        RequestBlocked = None
        IpBlocked = None

except ImportError:
    pass


YOUTUBE_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([\w\-]{6,20})",
    r"(?:https?://)?(?:www\.)?youtu\.be/([\w\-]{6,20})",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w\-]{6,20})",
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([\w\-]{6,20})",
]


def extract_video_id(url_or_text: str) -> Optional[str]:
    if not url_or_text:
        return None

    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, url_or_text, re.IGNORECASE)
        if match:
            return match.group(1)

    candidate = url_or_text.strip()
    if re.fullmatch(r"[\w\-]{6,20}", candidate):
        return candidate

    return None


def _is_exc_instance(exc: Exception, exc_type) -> bool:
    return exc_type is not None and isinstance(exc, exc_type)


def _get_entry_text(entry: Any) -> str:
    if isinstance(entry, dict):
        return (entry.get("text") or "").strip()
    return (getattr(entry, "text", "") or "").strip()


def _clean_transcript_text(text: str) -> str:
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_text(text: str) -> str:
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text

    truncated = text[:MAX_TRANSCRIPT_CHARS]
    last_period = truncated.rfind(".")
    if last_period > int(MAX_TRANSCRIPT_CHARS * 0.8):
        truncated = truncated[: last_period + 1]

    return truncated.rstrip() + " [Transcript truncated]"


def _normalize_entries(entries: Iterable[Any]) -> str:
    lines = []

    for entry in entries:
        raw = _get_entry_text(entry)
        if not raw:
            continue

        cleaned = _clean_transcript_text(raw)
        if cleaned:
            lines.append(cleaned)

    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _is_invalid_xml_error(exc: Exception) -> bool:
    if isinstance(exc, ParseError):
        return True

    message = str(exc).lower()
    return (
        "no element found" in message
        or ("xml" in message and "parse" in message)
    )


def _is_known_non_retryable_exception(exc: Exception) -> bool:
    return (
        _is_exc_instance(exc, NoTranscriptFound)
        or _is_exc_instance(exc, TranscriptsDisabled)
        or _is_exc_instance(exc, VideoUnavailable)
        or _is_exc_instance(exc, RequestBlocked)
        or _is_exc_instance(exc, IpBlocked)
    )


def _fetch_with_retries(fetch_fn, video_id: str):
    last_exc = None

    for attempt in range(TRANSCRIPT_FETCH_RETRIES + 1):
        try:
            return fetch_fn()
        except Exception as exc:
            last_exc = exc

            if _is_known_non_retryable_exception(exc):
                raise

            if _is_invalid_xml_error(exc):
                if attempt < TRANSCRIPT_FETCH_RETRIES:
                    time.sleep(TRANSCRIPT_FETCH_RETRY_DELAY_SEC)
                    continue
                raise RuntimeError(
                    f"Transcript fetch failed for video {video_id!r} because YouTube returned "
                    "an empty or invalid transcript response. This often happens for certain "
                    "videos, regions, rate limits, or temporary upstream changes."
                ) from exc

            if attempt < TRANSCRIPT_FETCH_RETRIES:
                time.sleep(TRANSCRIPT_FETCH_RETRY_DELAY_SEC)
                continue

            raise RuntimeError(
                f"Failed to fetch transcript for video {video_id!r}: {exc}"
            ) from exc

    raise RuntimeError(
        f"Failed to fetch transcript for video {video_id!r}: {last_exc}"
    ) from last_exc


def _fetch_transcript_entries(video_id: str):
    """
    For youtube-transcript-api 1.x:
    - prefer manual English
    - then generated English
    - then any available transcript
    """
    api = YouTubeTranscriptApi()
    transcript_list = _fetch_with_retries(lambda: api.list(video_id), video_id)

    transcript = None
    language_used = "unknown"

    try:
        transcript = transcript_list.find_manually_created_transcript(
            ["en", "en-US", "en-GB"]
        )
        language_used = "en (manual)"
    except Exception as exc:
        if _is_exc_instance(exc, NoTranscriptFound):
            pass
        else:
            raise

    if transcript is None:
        try:
            transcript = transcript_list.find_generated_transcript(
                ["en", "en-US", "en-GB"]
            )
            language_used = "en (auto-generated)"
        except Exception as exc:
            if _is_exc_instance(exc, NoTranscriptFound):
                pass
            else:
                raise

    if transcript is None:
        for t in transcript_list:
            transcript = t
            language_used = getattr(t, "language_code", "unknown")
            break

    if transcript is None:
        raise RuntimeError(
            "No transcript available for this video. The video may have captions disabled "
            "or no transcripts are available."
        )

    entries = _fetch_with_retries(lambda: transcript.fetch(), video_id)
    return list(entries), language_used


def extract_youtube_transcript(url_or_id: str) -> dict:
    """
    Extract transcript text from a YouTube video.

    Returns:
        {
            "video_id": str,
            "title": str,
            "text": str,
            "char_count": int,
            "language": str,
            "url": str,
        }
    """
    if not YT_TRANSCRIPT_AVAILABLE:
        raise RuntimeError(
            "youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"
        )

    video_id = extract_video_id(url_or_id)
    if not video_id:
        raise ValueError(
            f"Could not extract a valid YouTube video ID from: {url_or_id!r}"
        )

    try:
        entries, language_used = _fetch_transcript_entries(video_id)

    except Exception as exc:
        if (
            _is_exc_instance(exc, NoTranscriptFound)
            or _is_exc_instance(exc, TranscriptsDisabled)
        ):
            raise RuntimeError(
                f"Transcript not available for video {video_id!r}. "
                "The video owner may have disabled captions or no transcript exists."
            ) from exc

        if _is_exc_instance(exc, VideoUnavailable):
            raise RuntimeError(
                f"Video {video_id!r} is unavailable."
            ) from exc

        if _is_exc_instance(exc, RequestBlocked):
            raise RuntimeError(
                f"Transcript request was blocked for video {video_id!r}. "
                "This environment may be blocked by YouTube."
            ) from exc

        if _is_exc_instance(exc, IpBlocked):
            raise RuntimeError(
                f"Transcript request failed for video {video_id!r} because the IP appears to be blocked."
            ) from exc

        if _is_invalid_xml_error(exc):
            raise RuntimeError(
                f"Transcript fetch failed for video {video_id!r} because YouTube returned "
                "an empty or invalid transcript response. This often happens for certain "
                "videos, regions, rate limits, or temporary upstream changes."
            ) from exc

        if isinstance(exc, RuntimeError):
            raise

        raise RuntimeError(
            f"Failed to fetch transcript for video {video_id!r}: {exc}"
        ) from exc

    full_text = _normalize_entries(entries)

    if not full_text or len(full_text) < 50:
        raise RuntimeError(
            f"Transcript for video {video_id!r} is too short or empty to generate a quiz from."
        )

    truncated = _truncate_text(full_text)

    return {
        "video_id": video_id,
        "title": f"YouTube Video: {video_id}",
        "text": truncated,
        "char_count": len(truncated),
        "language": language_used,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }