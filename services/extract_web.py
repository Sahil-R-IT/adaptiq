"""
Web page content extraction.
Fetches a URL and extracts readable article text for quiz generation.
"""
import re
from typing import Optional
from urllib.parse import urlparse

MAX_EXTRACTED_CHARS = 12000
FETCH_TIMEOUT = 10

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


UNSAFE_SCHEMES = {"javascript", "file", "ftp", "data"}
BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported. Got: {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("URL has no host.")
    host = parsed.hostname or ""
    if host in BLOCKED_HOSTS or host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
        raise ValueError("Private/local URLs are not allowed.")


def _clean_text(raw: str) -> str:
    # Collapse whitespace
    text = re.sub(r"\r\n|\r", "\n", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _extract_with_bs4(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise tags
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", "iframe", "form", "button",
                     "svg", "img", "figure", "figcaption", "ad",
                     "[class*='ad-']", "[id*='cookie']"]):
        tag.decompose()

    # Try article/main first for cleaner extraction
    for selector in ["article", "main", "[role='main']", ".post-content",
                     ".article-body", ".entry-content", ".content"]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n")
            cleaned = _clean_text(text)
            if len(cleaned) > 300:
                return cleaned

    # Fallback: body text
    body = soup.find("body")
    if body:
        return _clean_text(body.get_text(separator="\n"))

    return _clean_text(soup.get_text(separator="\n"))


def extract_webpage(url: str) -> dict:
    """
    Fetch and extract readable text from a web page.

    Returns:
        {
            "title": str,
            "text": str,
            "url": str,
            "char_count": int,
        }

    Raises:
        RuntimeError on fetch/parse failure.
        ValueError on bad URL.
    """
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("requests library is not installed. Run: pip install requests")
    if not BS4_AVAILABLE:
        raise RuntimeError("beautifulsoup4 library is not installed. Run: pip install beautifulsoup4")

    _validate_url(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Request timed out fetching: {url}")
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Could not connect to: {url} ({exc})")
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"HTTP error fetching {url}: {exc}")

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise RuntimeError(f"Unsupported content type: {content_type}. Only HTML pages are supported.")

    html = response.text

    # Extract title
    title = url
    soup_title = BeautifulSoup(html, "html.parser")
    title_tag = soup_title.find("title")
    if title_tag:
        title = _clean_text(title_tag.get_text())

    text = _extract_with_bs4(html, url)

    if not text or len(text) < 100:
        raise RuntimeError("Could not extract meaningful text from this page. The page may be JavaScript-rendered or empty.")

    # Cap length
    truncated = text[:MAX_EXTRACTED_CHARS]
    if len(text) > MAX_EXTRACTED_CHARS:
        # Don't cut mid-sentence
        last_period = truncated.rfind(".")
        if last_period > MAX_EXTRACTED_CHARS * 0.8:
            truncated = truncated[:last_period + 1]
        truncated += "\n\n[Content truncated for quiz generation]"

    return {
        "title": title[:200],
        "text": truncated,
        "url": url,
        "char_count": len(truncated),
    }
