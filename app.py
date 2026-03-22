import json
import os
import random
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse, unquote
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

env_path = Path(__file__).resolve().parent / ".env"
print("DEBUG __file__:", __file__)
print("DEBUG ENV PATH:", env_path)
print("DEBUG ENV EXISTS:", env_path.exists())

loaded = load_dotenv(dotenv_path=env_path, override=True)
print("DEBUG DOTENV LOADED:", loaded)
print("DEBUG GEMINI KEY LOADED:", bool(os.getenv("GEMINI_API_KEY")))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()

if not GEMINI_MODEL:
    raise RuntimeError("GEMINI_MODEL is not set in .env")

try:
    from google import genai

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from services.source_detector import detect_source
from services.extract_web import extract_webpage
from services.extract_youtube import extract_youtube_transcript
from services.extract_files import (
    extract_document,
    extract_image_with_gemini,
    get_mime_type,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_DOCUMENT_EXTENSIONS,
)

app = Flask(__name__)

DATABASE_PATH = os.path.join(os.path.dirname(__file__), "database.db")
DEFAULT_QUIZ_SIZE = 10
MIN_QUIZ_SIZE = 5
MAX_QUIZ_SIZE = 40

ALLOW_MOCK_FALLBACK = False

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

ALL_QUESTION_TYPES = ["mcq", "fill_blank", "multi_select", "short_answer"]
ALL_DIFFICULTIES = ["easy", "medium", "hard"]
DIFFICULTY_ORDER = ["easy", "medium", "hard"]

QUESTION_TYPE_PATTERNS = {
    "mcq": [r"\bmcq\b", r"\bmultiple\s*choice\b", r"\bmultiple-choice\b"],
    "fill_blank": [r"\bfill\s*in\s*the\s*blank\b", r"\bfill\s*blank\b", r"\bblank\b"],
    "multi_select": [
        r"\bmulti\s*select\b",
        r"\bmultiple\s*select\b",
        r"\bselect\s*all\s*that\s*apply\b",
    ],
    "short_answer": [r"\bshort\s*answer\b", r"\bdescriptive\b", r"\bwritten\b"],
}

DIFFICULTY_PATTERNS = {
    "easy": [r"\beasy\b", r"\bsimple\b", r"\bbeginner\b", r"\bbasic\b", r"\bfoundation(?:al)?\b"],
    "medium": [r"\bmedium\b", r"\bintermediate\b", r"\bnormal\b", r"\bmoderate\b"],
    "hard": [
        r"\bhard\b",
        r"\badvanced\b",
        r"\bexpert\b",
        r"\bchallenging\b",
        r"\bdifficult\b",
        r"\btough\b",
    ],
}

NUMBER_WORDS = {
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty one": 21,
    "twenty-two": 22,
    "twenty three": 23,
    "twenty-four": 24,
    "twenty five": 25,
    "thirty": 30,
    "forty": 40,
}

DIFFICULTY_ALIASES = {
    "easy": [
        "easy", "eazy", "esay", "ez",
        "simple", "simpel",
        "beginner", "begginer", "beginer",
        "basic", "basic",
        "foundation", "foundational",
        "starter", "intro", "introductory"
    ],
    "medium": [
        "medium", "med", "mid", "middel", "medum", "mdium",
        "intermediate", "intermidiate", "intermedate",
        "normal", "moderate", "modrate",
        "standard", "average", "avg"
    ],
    "hard": [
        "hard", "hrd",
        "advanced", "advnced", "advance",
        "expert", "expart",
        "challenging", "chalenging",
        "difficult", "dificult", "difficultt",
        "diff", "tough", "tuf",
        "complex", "pro"
    ],
}

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def canonicalize_difficulty_token(token: str) -> Optional[str]:
    token = token.lower().strip()
    if not token:
        return None

    # Direct alias lookup first
    for difficulty, aliases in DIFFICULTY_ALIASES.items():
        if token == difficulty or token in aliases:
            return difficulty

    # Fuzzy fallback
    best_label = None
    best_score = 0.0

    for difficulty, aliases in DIFFICULTY_ALIASES.items():
        candidates = [difficulty] + aliases
        for candidate in candidates:
            score = similarity(token, candidate)
            if score > best_score:
                best_score = score
                best_label = difficulty

    # High threshold so random words do not get matched
    if best_score >= 0.82:
        return best_label

    return None

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     TEXT,
                topic          TEXT    NOT NULL,
                concept        TEXT    NOT NULL,
                question_text  TEXT    NOT NULL,
                question_type  TEXT    NOT NULL,
                difficulty     TEXT    NOT NULL DEFAULT 'easy',
                correct_answer TEXT    NOT NULL,
                user_answer    TEXT,
                is_correct     INTEGER NOT NULL DEFAULT 0,
                source_prompt  TEXT    DEFAULT '',
                created_at     TEXT    NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                session_id       TEXT PRIMARY KEY,
                topic            TEXT NOT NULL,
                source_prompt    TEXT DEFAULT '',
                requested_types  TEXT DEFAULT '[]',
                difficulty_plan  TEXT DEFAULT '[]',
                quiz_size        INTEGER NOT NULL DEFAULT 10,
                primary_level    TEXT NOT NULL DEFAULT 'easy',
                is_adaptive      INTEGER NOT NULL DEFAULT 0,
                correct_count    INTEGER NOT NULL DEFAULT 0,
                total_questions  INTEGER NOT NULL DEFAULT 0,
                accuracy         REAL    NOT NULL DEFAULT 0,
                xp_earned        INTEGER NOT NULL DEFAULT 0,
                source_type      TEXT    DEFAULT 'topic',
                source_title     TEXT    DEFAULT '',
                source_url       TEXT    DEFAULT '',
                source_file_name TEXT    DEFAULT '',
                source_excerpt   TEXT    DEFAULT '',
                created_at       TEXT NOT NULL
            )
            """
        )

        ensure_column(conn, "quiz_attempts", "session_id TEXT")
        ensure_column(conn, "quiz_attempts", "difficulty TEXT NOT NULL DEFAULT 'easy'")
        ensure_column(conn, "quiz_attempts", "source_prompt TEXT DEFAULT ''")

        ensure_column(conn, "quiz_sessions", "difficulty_plan TEXT DEFAULT '[]'")
        ensure_column(conn, "quiz_sessions", "primary_level TEXT NOT NULL DEFAULT 'easy'")
        ensure_column(conn, "quiz_sessions", "source_type TEXT DEFAULT 'topic'")
        ensure_column(conn, "quiz_sessions", "source_title TEXT DEFAULT ''")
        ensure_column(conn, "quiz_sessions", "source_url TEXT DEFAULT ''")
        ensure_column(conn, "quiz_sessions", "source_file_name TEXT DEFAULT ''")
        ensure_column(conn, "quiz_sessions", "source_excerpt TEXT DEFAULT ''")

        conn.commit()


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """
    Unwrap Google redirect URLs so extraction hits the real target page.
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)
        if "google.com" in parsed.netloc and parsed.path in ("/url", "/url/"):
            qs = parse_qs(parsed.query)
            for key in ("url", "q"):
                values = qs.get(key)
                if values and values[0]:
                    return unquote(values[0])
    except Exception:
        pass

    return url


# ---------------------------------------------------------------------------
# Prompt / request parsing
# ---------------------------------------------------------------------------

def normalize_difficulty_words(text: str) -> str:
    words = re.findall(r"\b\w+\b|\W+", text.lower())
    normalized_parts = []

    for part in words:
        if re.fullmatch(r"\w+", part):
            mapped = canonicalize_difficulty_token(part)
            normalized_parts.append(mapped if mapped else part)
        else:
            normalized_parts.append(part)

    return "".join(normalized_parts)

def normalize_number_words(text: str) -> str:
    """
    Replace number words with digits for easier parsing.
    """
    normalized = text.lower()

    # Longest first to avoid partial replacement problems.
    ordered_words = sorted(NUMBER_WORDS.items(), key=lambda item: len(item[0]), reverse=True)
    for word, value in ordered_words:
        normalized = re.sub(rf"\b{re.escape(word)}\b", str(value), normalized, flags=re.IGNORECASE)

    return normalized


def validate_explicit_size(size: int, source_label: str) -> int:
    if size < MIN_QUIZ_SIZE or size > MAX_QUIZ_SIZE:
        raise ValueError(
            f"{source_label} must be between {MIN_QUIZ_SIZE} and {MAX_QUIZ_SIZE} questions."
        )
    return size


def parse_explicit_total_count(prompt: str) -> Optional[int]:
    """
    Extract an explicit total quiz count only when the prompt clearly expresses a total.
    """
    lower = normalize_difficulty_words(normalize_number_words(prompt.strip().lower()))

    total_patterns = [
        r"\b(?:i\s+want|give\s+me|create|generate|make)\s+(\d{1,2})\s*(?:questions?|qs?|mcqs?)\b",
        r"\b(?:total\s*(?:of)?\s*)(\d{1,2})\s*(?:questions?|qs?|mcqs?)?\b",
        r"\b(\d{1,2})\s*(?:questions?|qs?|mcqs?)\s*(?:on|about|for|with|in|:)\b",
        r"\b(\d{1,2})\s*(?:questions?|qs?|mcqs?)\s*please\b",
        r"\b(?:quiz\s+of|quiz\s+with)\s+(\d{1,2})\s*(?:questions?|qs?|mcqs?)?\b",
    ]

    for pattern in total_patterns:
        match = re.search(pattern, lower, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def parse_explicit_difficulty_counts(prompt: str) -> dict[str, int]:
    """
    Extract explicit counts like:
      - 10 hard
      - 12 easy questions
      - medium 5
      - 5 questions are hard
      - 5 easy, 3 medium, 2 hard
    """
    lower = normalize_difficulty_words(normalize_number_words(prompt.lower()))
    matches: list[tuple[int, int, str, int]] = []

    for difficulty, aliases in DIFFICULTY_ALIASES.items():
        alias_pattern = "|".join(re.escape(a) for a in aliases)

        patterns = [
            rf"\b(?P<count>\d{{1,2}})\s*(?:questions?|qs?|mcqs?)?\s*(?:of\s+)?(?P<diff>{alias_pattern})\b",
            rf"\b(?P<count>\d{{1,2}})\s*(?:questions?|qs?|mcqs?)\s*(?:are|is)\s*(?P<diff>{alias_pattern})\b",
            rf"\b(?P<diff>{alias_pattern})\s*(?P<count>\d{{1,2}})\s*(?:questions?|qs?|mcqs?)?\b",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, lower, re.IGNORECASE):
                start, end = match.span()
                count = int(match.group("count"))
                matches.append((start, end, difficulty, count))

    if not matches:
        return {}

    matches.sort(key=lambda item: (item[0], item[1]))
    consumed_ranges: list[tuple[int, int]] = []
    counts = {"easy": 0, "medium": 0, "hard": 0}

    for start, end, difficulty, count in matches:
        overlaps = any(not (end <= s or start >= e) for s, e in consumed_ranges)
        if overlaps:
            continue
        counts[difficulty] += count
        consumed_ranges.append((start, end))

    return {k: v for k, v in counts.items() if v > 0}


def resolve_quiz_size(
    explicit_total: Optional[int],
    explicit_difficulty_counts: dict[str, int],
) -> int:
    """
    Decide final quiz size with strict validation.

    Rules:
    1. If both explicit total and explicit difficulty counts exist, they must match.
    2. If only explicit difficulty counts exist, total = their sum.
    3. If only explicit total exists, use it.
    4. Otherwise use DEFAULT_QUIZ_SIZE.
    """
    diff_sum = sum(explicit_difficulty_counts.values())
    has_diff_counts = diff_sum > 0
    has_total = explicit_total is not None

    if has_diff_counts and has_total:
        validate_explicit_size(explicit_total, "Requested total quiz size")
        validate_explicit_size(diff_sum, "Requested difficulty count total")

        if diff_sum != explicit_total:
            raise ValueError(
                f"The difficulty counts you specified add up to {diff_sum}, "
                f"but your total quiz size is {explicit_total}. "
                "Make them match."
            )
        return explicit_total

    if has_diff_counts:
        return validate_explicit_size(diff_sum, "Requested difficulty count total")

    if has_total:
        return validate_explicit_size(explicit_total, "Requested total quiz size")

    return DEFAULT_QUIZ_SIZE


def parse_question_types(prompt: str) -> list[str]:
    lower = prompt.lower()
    found = []
    for qtype, patterns in QUESTION_TYPE_PATTERNS.items():
        if any(re.search(pattern, lower) for pattern in patterns):
            found.append(qtype)
    return found or ALL_QUESTION_TYPES[:]


def parse_requested_difficulties(prompt: str) -> list[str]:
    """
    Return difficulty names mentioned in the prompt, regardless of count.
    """
    lower = normalize_difficulty_words(prompt.lower())
    found = []
    for difficulty, patterns in DIFFICULTY_PATTERNS.items():
        if any(re.search(pattern, lower) for pattern in patterns):
            found.append(difficulty)
    return [d for d in DIFFICULTY_ORDER if d in found]


def extract_topic_from_prompt(prompt: str) -> str:
    original = prompt.strip()
    lower = normalize_number_words(original.lower())

    # First try targeted capture after clear topic markers.
    topic_match = re.search(
        r"\b(?:on|about|for)\s+([a-zA-Z0-9][\w\s\-+/&()]{1,120})$",
        lower,
        flags=re.IGNORECASE,
    )
    if topic_match:
        candidate = topic_match.group(1).strip(" ,.-_")
        if candidate:
            return candidate.title()

    cleaned = original

    removal_patterns = [
        r"\bplease\b",
        r"\bgive\s+me\b",
        r"\bgive\b",
        r"\bgave\s+me\b",
        r"\bi\s+want\b",
        r"\bmake\b",
        r"\bcreate\b",
        r"\bgenerate\b",
        r"\bquiz\b",
        r"\bquizzes\b",
        r"\bquestion\b",
        r"\bquestions\b",
        r"\bmcqs?\b",
        r"\brandom\b",
        r"\bdefault\b",
        r"\bstyle\b",
        r"\bonly\b",
        r"\bwith\b",
        r"\band\b",
        r"\busing\b",
        r"\buse\b",
        r"\bfor\b",
        r"\babout\b",
        r"\bon\b",
        r"\blevel\b",
        r"\btype\b",
        r"\bof\b",
        r"\btopic\b",
        r"\bmixed\b",
        r"\bweight(?:ed)?\b",
        r"\bfrom\b",
        r"\bthis\b",
        r"\bthe\b",
        r"\ban?\b",
        r"https?://\S+",
    ]

    for patterns in QUESTION_TYPE_PATTERNS.values():
        removal_patterns.extend(patterns)
    for patterns in DIFFICULTY_PATTERNS.values():
        removal_patterns.extend(patterns)

    removal_patterns.extend(
        [
            r"\b\d{1,2}\s*(?:questions?|qs?|mcqs?)\b",
            r"\b\d{1,2}\b",
        ]
    )
    removal_patterns.extend([rf"\b{re.escape(word)}\b" for word in NUMBER_WORDS])

    for pattern in removal_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[^\w\s\-+/&()]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-_")

    if not cleaned:
        cleaned = original.strip()

    lower_cleaned = cleaned.lower()
    for prefix in ["about ", "on ", "topic ", "for "]:
        if lower_cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break

    return cleaned or "General Knowledge"


def parse_quiz_request(prompt: str) -> dict[str, Any]:
    source_prompt = (prompt or "").strip()
    if not source_prompt:
        raise ValueError("prompt is required")

    explicit_total = parse_explicit_total_count(source_prompt)
    explicit_difficulty_counts = parse_explicit_difficulty_counts(source_prompt)
    quiz_size = resolve_quiz_size(explicit_total, explicit_difficulty_counts)

    return {
        "source_prompt": source_prompt,
        "topic": extract_topic_from_prompt(source_prompt),
        "quiz_size": quiz_size,
        "question_types": parse_question_types(source_prompt),
        "requested_difficulties": parse_requested_difficulties(source_prompt),
        "explicit_difficulty_counts": explicit_difficulty_counts,
    }


# ---------------------------------------------------------------------------
# DB analytics helpers
# ---------------------------------------------------------------------------


def get_used_questions(topic: str) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT question_text FROM quiz_attempts
            WHERE LOWER(topic) = LOWER(?)
            ORDER BY id DESC LIMIT 50
            """,
            (topic,),
        ).fetchall()
    return [row["question_text"] for row in rows]


def get_topic_attempt_totals(topic: str) -> tuple[int, int]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
            FROM quiz_attempts WHERE LOWER(topic) = LOWER(?)
            """,
            (topic,),
        ).fetchone()
    return int(row["total"] or 0), int(row["correct"] or 0)


def get_topic_accuracy(topic: str) -> float:
    total, correct = get_topic_attempt_totals(topic)
    return (correct / total * 100.0) if total else 0.0


def get_recent_wrong_items(topic: str, limit: int = 10) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT topic, concept, question_text, correct_answer, user_answer, difficulty, question_type, created_at
            FROM quiz_attempts
            WHERE LOWER(topic) = LOWER(?) AND is_correct = 0
            ORDER BY datetime(created_at) DESC, id DESC LIMIT ?
            """,
            (topic, limit),
        ).fetchall()
    return [
        {
            "topic": row["topic"],
            "concept": row["concept"],
            "question": row["question_text"],
            "correct_answer": row["correct_answer"],
            "user_answer": row["user_answer"],
            "difficulty": row["difficulty"],
            "question_type": row["question_type"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def weighted_counts(total: int, weights: dict[str, float]) -> dict[str, int]:
    keys = list(weights.keys())
    counts = {key: 0 for key in keys}
    if total <= 0:
        return counts

    raw = {key: weights[key] * total for key in keys}
    for key in keys:
        counts[key] = int(raw[key])

    assigned = sum(counts.values())
    remainders = sorted(((raw[key] - counts[key], key) for key in keys), reverse=True)
    idx = 0
    while assigned < total:
        key = remainders[idx % len(remainders)][1]
        counts[key] += 1
        assigned += 1
        idx += 1
    return counts


def build_difficulty_plan(
    topic: str,
    quiz_size: int,
    requested_difficulties: list[str],
    adaptive: bool,
    explicit_difficulty_counts: Optional[dict[str, int]] = None,
) -> list[str]:
    """
    Build a shuffled list of difficulties of length quiz_size.

    Priority:
    1. explicit counts
    2. mentioned difficulty names only
    3. adaptive retry logic
    4. default 70/20/10
    """
    if explicit_difficulty_counts:
        plan: list[str] = []
        for diff in DIFFICULTY_ORDER:
            plan.extend([diff] * explicit_difficulty_counts.get(diff, 0))

        if len(plan) != quiz_size:
            raise ValueError(
                f"Internal difficulty plan error: plan length {len(plan)} does not match quiz size {quiz_size}."
            )

        random.shuffle(plan)
        return plan

    if requested_difficulties:
        if len(requested_difficulties) == 1:
            return [requested_difficulties[0]] * quiz_size

        equal = 1 / len(requested_difficulties)
        cnts = weighted_counts(quiz_size, {d: equal for d in requested_difficulties})
        plan = []
        for diff in DIFFICULTY_ORDER:
            plan.extend([diff] * cnts.get(diff, 0))
        random.shuffle(plan)
        return plan[:quiz_size]

    if adaptive and get_recent_wrong_items(topic, limit=20):
        cnts = weighted_counts(quiz_size, {"easy": 0.15, "medium": 0.25, "hard": 0.60})
        plan = (
            ["easy"] * cnts["easy"]
            + ["medium"] * cnts["medium"]
            + ["hard"] * cnts["hard"]
        )
        random.shuffle(plan)
        return plan[:quiz_size]

    cnts = weighted_counts(quiz_size, {"easy": 0.70, "medium": 0.20, "hard": 0.10})
    plan = (
        ["easy"] * cnts["easy"]
        + ["medium"] * cnts["medium"]
        + ["hard"] * cnts["hard"]
    )
    random.shuffle(plan)
    return plan[:quiz_size]


def primary_level_from_plan(plan: list[str]) -> str:
    if not plan:
        return "easy"
    counts = Counter(plan)
    return max(DIFFICULTY_ORDER, key=lambda d: (counts[d], -DIFFICULTY_ORDER.index(d)))


def get_weak_concepts(topic: Optional[str] = None, limit: int = 8) -> list[dict[str, Any]]:
    query = """
        SELECT topic, concept,
               COUNT(*) AS total,
               SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS wrong
        FROM quiz_attempts
    """
    params: tuple[Any, ...] = ()
    if topic:
        query += " WHERE LOWER(topic) = LOWER(?)"
        params = (topic,)
    query += " GROUP BY topic, concept HAVING total >= 1"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    weak = []
    for row in rows:
        total = int(row["total"] or 0)
        wrong = int(row["wrong"] or 0)
        if total <= 0:
            continue
        error_rate = wrong / total
        if error_rate > 0.5:
            weak.append(
                {
                    "topic": row["topic"],
                    "concept": row["concept"],
                    "total": total,
                    "wrong": wrong,
                    "error_rate": round(error_rate * 100, 1),
                }
            )

    weak.sort(key=lambda item: (item["error_rate"], item["wrong"]), reverse=True)
    return weak[:limit]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_prompt(
    spec: dict[str, Any],
    weak_concepts: list[dict[str, Any]],
    used_questions: list[str],
    focus_context: list[dict[str, Any]],
    source_text: Optional[str] = None,
    source_title: Optional[str] = None,
    source_type: str = "topic",
) -> str:
    topic = spec["topic"]
    quiz_size = spec["quiz_size"]
    question_types = spec["question_types"]
    difficulty_plan = spec["difficulty_plan"]
    adaptive = spec.get("adaptive", False)

    difficulty_counts = Counter(difficulty_plan)
    mix_line = ", ".join(
        f"{k}={difficulty_counts[k]}" for k in DIFFICULTY_ORDER if difficulty_counts.get(k)
    )

    ordered_plan = "\n".join(
        f"{idx + 1}. {difficulty}"
        for idx, difficulty in enumerate(difficulty_plan)
    )

    if set(question_types) == set(ALL_QUESTION_TYPES):
        type_instruction = (
            "Use a varied mix of question types: mcq, fill_blank, multi_select, short_answer."
        )
    else:
        type_instruction = (
            "Use ONLY these question types: " + ", ".join(question_types) + "."
        )

    weak_note = ""
    if weak_concepts:
        weak_note = "\nEmphasize these weak concepts: " + ", ".join(
            f"{item['concept']} ({item['error_rate']}% error)" for item in weak_concepts[:4]
        )

    focus_note = ""
    if focus_context:
        lines = [
            f"- concept={item.get('concept', 'General')} | prev_q={item.get('question', '')} "
            f"| wrong={item.get('user_answer', '')} | expected={item.get('correct_answer', '')}"
            for item in focus_context[:6]
        ]
        focus_note = "\nAdaptive retry focus:\n" + "\n".join(lines)

    used_note = ""
    if used_questions:
        used_note = "\nDo NOT repeat or closely paraphrase these previous questions:\n- " + "\n- ".join(
            used_questions[:15]
        )

    adaptive_note = "\nThis is an adaptive retry quiz." if adaptive else ""

    if source_text:
        source_label = {
            "website": "website article",
            "youtube": "YouTube video transcript",
            "image": "image content",
            "document": "uploaded document",
        }.get(source_type, "source content")
        title_suffix = f" titled '{source_title}'" if source_title else ""
        source_section = (
            f"Base the quiz ONLY on the following {source_label}{title_suffix}. "
            "Do NOT invent facts outside the source. "
            "Do NOT ask about information not present in the source.\n\n"
            f"--- SOURCE START ---\n{source_text[:12000]}\n--- SOURCE END ---\n\n"
            f'Use this topic label for all questions: "{topic}"'
        )
    else:
        source_section = f'Generate questions about the topic: "{topic}" only.'

    return f"""You are a strict quiz generator.

Output ONLY a valid JSON array.
Do NOT output markdown.
Do NOT output prose.
Do NOT output anything before or after the JSON array.

STRICT REQUIREMENTS:
- Total questions: EXACTLY {quiz_size}
- Allowed question types: {", ".join(question_types)}
- Difficulty distribution must match exactly: {mix_line}
- Difficulty by question index must match this exact order:
{ordered_plan}

{type_instruction}

{source_section}{adaptive_note}{weak_note}{focus_note}{used_note}

Each JSON object must contain EXACTLY these keys:
topic, concept, question_type, difficulty, question, options, correct_answer, explanation

Rules:
1. topic must always be "{topic}"
2. question_type must be one of: mcq, fill_blank, multi_select, short_answer
3. difficulty must match the required difficulty for that question index
4. mcq and multi_select:
   - options must contain EXACTLY 4 items
   - format each item as "A. ...", "B. ...", "C. ...", "D. ..."
5. multi_select correct_answer must be comma-separated letters like "A,C"
6. fill_blank and short_answer:
   - options must be []
   - correct_answer must be comma-separated accepted keywords or variants
7. No duplicates
8. No empty fields
9. Keep questions clear, grounded, and factually correct

Difficulty guidance:
- easy = basic recall or simple understanding
- medium = application or interpretation
- hard = reasoning, distinction, or deeper understanding

Return the JSON array only.
""".strip()


# ---------------------------------------------------------------------------
# JSON extraction / validation
# ---------------------------------------------------------------------------


def _extract_json_array(raw: str) -> list[dict]:
    if not raw or not raw.strip():
        raise ValueError("Gemini returned an empty response.")

    cleaned = raw.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    start = cleaned.find("[")
    if start == -1:
        raise ValueError(f"No JSON array start found in Gemini response:\n{cleaned}")

    depth = 0
    in_string = False
    escape = False
    end = -1

    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        raise ValueError(f"No complete JSON array found in Gemini response:\n{cleaned}")

    json_text = cleaned[start:end + 1]

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini returned invalid JSON: {e}\nRaw:\n{json_text}")

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array but got {type(parsed).__name__}")

    return parsed


def build_mock_question(
    topic: str, qtype: str, difficulty: str, concept_seed: int
) -> dict[str, Any]:
    concepts = [
        "Core ideas",
        "Definitions",
        "Application",
        "Reasoning",
        "Examples",
        "Practice",
        "Patterns",
        "Problem solving",
        "Common mistakes",
        "Review",
    ]
    concept = concepts[concept_seed % len(concepts)]
    stem = {"easy": "basic", "medium": "applied", "hard": "challenging"}[difficulty]

    if qtype == "mcq":
        return {
            "topic": topic,
            "concept": concept,
            "question_type": "mcq",
            "difficulty": difficulty,
            "question": f"Which option best matches a {stem} idea in {topic}?",
            "options": ["A. Wrong distractor", "B. Correct concept", "C. Unrelated", "D. Misleading"],
            "correct_answer": "B",
            "explanation": "B is the best answer.",
        }
    if qtype == "multi_select":
        return {
            "topic": topic,
            "concept": concept,
            "question_type": "multi_select",
            "difficulty": difficulty,
            "question": f"Select all statements that fit {topic} at {difficulty} level.",
            "options": ["A. Valid one", "B. Valid two", "C. Distractor one", "D. Distractor two"],
            "correct_answer": "A,B",
            "explanation": "A and B are correct.",
        }
    if qtype == "fill_blank":
        return {
            "topic": topic,
            "concept": concept,
            "question_type": "fill_blank",
            "difficulty": difficulty,
            "question": f"Complete: An important {stem} idea in {topic} is ______.",
            "options": [],
            "correct_answer": "concept,principle,rule",
            "explanation": "Any synonym counts.",
        }
    return {
        "topic": topic,
        "concept": concept,
        "question_type": "short_answer",
        "difficulty": difficulty,
        "question": f"Briefly explain one {stem} concept in {topic}.",
        "options": [],
        "correct_answer": "concept,principle,example,application",
        "explanation": "Any valid concept counts.",
    }


def mock_questions(spec: dict[str, Any]) -> list[dict[str, Any]]:
    topic = spec["topic"]
    question_types = spec["question_types"]
    difficulty_plan = spec["difficulty_plan"]
    quiz_size = spec["quiz_size"]
    return [
        build_mock_question(
            topic,
            question_types[idx % len(question_types)],
            difficulty_plan[idx] if idx < len(difficulty_plan) else "easy",
            idx,
        )
        for idx in range(quiz_size)
    ]


def _normalize_options(options: list[Any]) -> list[str]:
    return [str(opt).strip() for opt in options if str(opt).strip()]


def _validate_questions(
    questions: list[dict[str, Any]],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    quiz_size = spec["quiz_size"]
    allowed_types = spec["question_types"]
    allowed_type_set = set(allowed_types)
    fallback_type = allowed_types[0]
    topic = spec["topic"]
    plan = spec["difficulty_plan"]

    normalized: list[dict[str, Any]] = []
    seen_questions: set[str] = set()

    for idx, item in enumerate(questions):
        if not isinstance(item, dict):
            continue

        target_difficulty = plan[idx] if idx < len(plan) else "easy"
        qtype = str(item.get("question_type") or "mcq").strip()
        if qtype not in ALL_QUESTION_TYPES:
            qtype = "mcq"
        if qtype not in allowed_type_set:
            qtype = fallback_type

        options = _normalize_options(item.get("options") if isinstance(item.get("options"), list) else [])
        question_text = str(item.get("question") or "").strip()
        concept = str(item.get("concept") or "General").strip()
        correct_answer = str(item.get("correct_answer") or "").strip()
        explanation = str(item.get("explanation") or "").strip()

        if not question_text or not correct_answer or not explanation:
            continue

        # Simple near-duplicate defense
        normalized_question_key = re.sub(r"\s+", " ", question_text.lower()).strip()
        if normalized_question_key in seen_questions:
            continue

        if qtype in {"mcq", "multi_select"}:
            if len(options) != 4:
                continue
        else:
            options = []

        q = {
            "topic": topic,
            "concept": concept or "General",
            "question_type": qtype,
            "difficulty": target_difficulty,
            "question": question_text,
            "options": options,
            "correct_answer": correct_answer,
            "explanation": explanation,
        }

        seen_questions.add(normalized_question_key)
        normalized.append(q)

        if len(normalized) >= quiz_size:
            break

    while len(normalized) < quiz_size:
        idx = len(normalized)
        filler = build_mock_question(
            topic,
            spec["question_types"][idx % len(spec["question_types"])],
            plan[idx] if idx < len(plan) else "easy",
            idx,
        )
        filler_key = re.sub(r"\s+", " ", filler["question"].lower()).strip()
        if filler_key not in seen_questions:
            normalized.append(filler)
            seen_questions.add(filler_key)

    return normalized[:quiz_size]


def call_gemini(prompt: str) -> list[dict]:
    if not GEMINI_AVAILABLE:
        raise RuntimeError("Google GenAI SDK is not installed.")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    app.logger.info("Calling Gemini API...")
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    raw = (getattr(response, "text", "") or "").strip()
    app.logger.info("Gemini response received.")
    app.logger.debug("Raw Gemini response:\n%s", raw)
    return _extract_json_array(raw)

class GeminiQuotaError(RuntimeError):
    pass

def is_gemini_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    quota_markers = [
        "quota",
        "rate limit",
        "429",
        "resource exhausted",
        "too many requests",
    ]
    return any(marker in msg for marker in quota_markers)

def generate_questions(
    spec: dict[str, Any],
    focus_context: list[dict[str, Any]],
    source_text: Optional[str] = None,
    source_title: Optional[str] = None,
    source_type: str = "topic",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    topic = spec["topic"]
    weak = get_weak_concepts(topic)
    used = get_used_questions(topic)
    prompt = build_prompt(spec, weak, used, focus_context, source_text, source_title, source_type)

    app.logger.info(
        "Generating quiz: topic=%s source_type=%s difficulties=%s size=%s",
        topic,
        source_type,
        spec["difficulty_plan"],
        spec["quiz_size"],
    )

    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            questions = call_gemini(prompt)
            return _validate_questions(questions, spec), weak
        except Exception as exc:
            app.logger.exception("Gemini generation failed: %s", exc)

            if ALLOW_MOCK_FALLBACK:
                return _validate_questions(mock_questions(spec), spec), weak

            if is_gemini_quota_error(exc):
                raise GeminiQuotaError(
                    "Gemini API quota exceeded. Please wait and try again later, "
                    "or use a new API key / higher quota plan."
                )

            raise RuntimeError(f"Gemini call failed: {exc}")

    if ALLOW_MOCK_FALLBACK:
        return _validate_questions(mock_questions(spec), spec), weak

    if not GEMINI_AVAILABLE:
        raise RuntimeError("Google GenAI SDK is not installed.")

    raise RuntimeError("GEMINI_API_KEY is not set.")

# ---------------------------------------------------------------------------
# Answer evaluation
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 4:
        return 999
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[len(b)]


def _tokenize_text(text: str) -> list[str]:
    return re.findall(r"\b[a-z0-9]+\b", text.lower())


def evaluate_answer(question: dict[str, Any], user_answer: str) -> bool:
    correct = str(question.get("correct_answer", "")).strip().lower()
    given = str(user_answer).strip().lower()
    qtype = question.get("question_type", "")

    if qtype == "multi_select":
        c_set = {x.strip() for x in correct.split(",") if x.strip()}
        g_set = {x.strip() for x in given.split(",") if x.strip()}
        return c_set == g_set

    if qtype == "short_answer":
        given_tokens = set(_tokenize_text(given))
        keywords = [kw.strip().lower() for kw in correct.split(",") if kw.strip()]
        for kw in keywords:
            kw_tokens = set(_tokenize_text(kw))
            if kw_tokens and kw_tokens.issubset(given_tokens):
                return True
        return False

    if qtype == "fill_blank":
        accepted = [kw.strip().lower() for kw in correct.split(",") if kw.strip()]
        for token in accepted:
            if token == given:
                return True
            if token in given.split():
                return True
        for token in accepted:
            for candidate in [given] + given.split():
                if _levenshtein(candidate, token) <= 2:
                    return True
        return False

    given_letter = given.split(".")[0].strip()
    return correct == given_letter


def calculate_xp(results: list[dict[str, Any]], is_adaptive: bool) -> int:
    diff_bonus = {"easy": 0, "medium": 3, "hard": 6}
    xp = 0
    correct_count = 0
    for item in results:
        if item["is_correct"]:
            correct_count += 1
            xp += 10 + diff_bonus.get(item.get("difficulty", "easy"), 0)
    total = len(results)
    accuracy = (correct_count / total * 100) if total else 0
    if accuracy == 100:
        xp += 20
    elif accuracy >= 80:
        xp += 10
    if is_adaptive and correct_count > 0:
        xp += 5
    return xp


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------


def get_current_streak() -> int:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM quiz_sessions ORDER BY day DESC"
        ).fetchall()
    days = [datetime.strptime(row["day"], "%Y-%m-%d").date() for row in rows if row["day"]]
    if not days:
        return 0
    today = utcnow().date()
    if days[0] not in {today, today - timedelta(days=1)}:
        return 0
    streak = 1
    prev_day = days[0]
    for day in days[1:]:
        if prev_day - day == timedelta(days=1):
            streak += 1
            prev_day = day
        elif day == prev_day:
            continue
        else:
            break
    return streak


def mastery_label(total_questions: int, accuracy: float) -> str:
    if total_questions >= 40 and accuracy >= 90:
        return "Master"
    if total_questions >= 20 and accuracy >= 80:
        return "Expert"
    if total_questions >= 10 and accuracy >= 65:
        return "Intermediate"
    return "Beginner"


def get_topic_stats(limit: int = 50) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT topic,
                   COUNT(*) AS total_questions,
                   COALESCE(SUM(is_correct), 0) AS correct_answers,
                   MAX(created_at) AS last_attempt
            FROM quiz_attempts
            GROUP BY topic
            ORDER BY MAX(created_at) DESC
            """
        ).fetchall()

    stats = []
    for row in rows:
        total_questions = int(row["total_questions"] or 0)
        correct_answers = int(row["correct_answers"] or 0)
        accuracy = (
            round((correct_answers / total_questions) * 100, 1) if total_questions else 0.0
        )
        stats.append(
            {
                "topic": row["topic"],
                "total_questions": total_questions,
                "correct_answers": correct_answers,
                "accuracy": accuracy,
                "mastery": mastery_label(total_questions, accuracy),
                "last_attempt": row["last_attempt"],
            }
        )

    stats.sort(key=lambda item: (item["accuracy"], item["total_questions"]), reverse=True)
    return stats[:limit]


def get_strong_topics(limit: int = 6) -> list[dict[str, Any]]:
    stats = get_topic_stats(limit=100)
    return [
        item for item in stats if item["accuracy"] >= 70 and item["total_questions"] >= 5
    ][:limit]


def get_weak_topics(limit: int = 6) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT topic, concept, COUNT(*) AS total,
                   SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS wrong
            FROM quiz_attempts GROUP BY topic, concept HAVING total >= 1
            """
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"topic": "", "total": 0, "wrong": 0, "worst_concepts": []}
    )
    for row in rows:
        topic = row["topic"]
        concept = row["concept"]
        total = int(row["total"] or 0)
        wrong = int(row["wrong"] or 0)
        error_rate = (wrong / total * 100.0) if total else 0.0
        entry = grouped[topic]
        entry["topic"] = topic
        entry["total"] += total
        entry["wrong"] += wrong
        entry["worst_concepts"].append(
            {"concept": concept, "error_rate": round(error_rate, 1), "wrong": wrong, "total": total}
        )

    weak_topics = []
    for topic, entry in grouped.items():
        total = entry["total"]
        wrong = entry["wrong"]
        if total <= 0:
            continue
        error_rate = round((wrong / total) * 100, 1)
        if error_rate < 30:
            continue
        entry["worst_concepts"].sort(
            key=lambda x: (x["error_rate"], x["wrong"]), reverse=True
        )
        weak_topics.append(
            {
                "topic": topic,
                "error_rate": error_rate,
                "wrong": wrong,
                "total": total,
                "top_concepts": entry["worst_concepts"][:3],
            }
        )

    weak_topics.sort(key=lambda x: (x["error_rate"], x["wrong"]), reverse=True)
    return weak_topics[:limit]


def get_analytics_snapshot() -> dict[str, Any]:
    with get_db() as conn:
        attempts = conn.execute(
            "SELECT COUNT(*) AS total_questions, COALESCE(SUM(is_correct), 0) AS correct_answers FROM quiz_attempts"
        ).fetchone()
        sessions = conn.execute(
            "SELECT COUNT(*) AS total_quizzes, COALESCE(SUM(xp_earned), 0) AS total_xp FROM quiz_sessions"
        ).fetchone()

    total_questions = int(attempts["total_questions"] or 0)
    correct_answers = int(attempts["correct_answers"] or 0)
    accuracy = round((correct_answers / total_questions) * 100, 1) if total_questions else 0.0

    return {
        "summary": {
            "total_xp": int(sessions["total_xp"] or 0),
            "accuracy": accuracy,
            "streak": get_current_streak(),
            "quizzes": int(sessions["total_quizzes"] or 0),
            "questions": total_questions,
            "correct_answers": correct_answers,
        },
        "strong_topics": get_strong_topics(limit=6),
        "weak_topics": get_weak_topics(limit=6),
    }


# ---------------------------------------------------------------------------
# File upload helper
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_DOCUMENT_EXTENSIONS


def _allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _save_upload(file_storage) -> tuple[str, str]:
    original_name = file_storage.filename or "upload"
    safe_name = secure_filename(original_name)
    if not safe_name:
        safe_name = "upload.bin"
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    save_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file_storage.save(save_path)
    return save_path, safe_name


# ---------------------------------------------------------------------------
# Webpage extraction wrapper
# ---------------------------------------------------------------------------


def extract_webpage_safe(url: str) -> dict[str, Any]:
    """
    Normalize the URL and try extraction.
    This does graceful failure handling.
    It does NOT provide a true alternate extractor unless your extract_webpage()
    implementation already does that internally.
    """
    real_url = normalize_url(url)

    try:
        result = extract_webpage(real_url)
        text = (result.get("text") or "").strip()
        title = (result.get("title") or "").strip()

        # Thin content is often useless for quiz generation.
        word_count = len(re.findall(r"\b\w+\b", text))
        if word_count >= 60:
            return {"text": text, "title": title, "url": real_url}

        app.logger.warning("Thin webpage extraction for %s (word_count=%s)", real_url, word_count)
        return {
            "text": text,
            "title": title,
            "url": real_url,
            "_thin": True,
        }

    except Exception as exc:
        app.logger.warning("extract_webpage failed for %s: %s", real_url, exc)
        return {
            "text": "",
            "title": "",
            "url": real_url,
            "_error": str(exc),
        }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/generate-quiz", methods=["POST"])
def generate_quiz() -> Any:
    if request.content_type and "multipart/form-data" in request.content_type:
        prompt_text = (request.form.get("prompt") or request.form.get("topic") or "").strip()
        adaptive = request.form.get("adaptive", "false").lower() == "true"
        raw_focus = request.form.get("focus_context", "[]")
        try:
            focus_context = json.loads(raw_focus)
        except Exception:
            focus_context = []
        uploaded_file = request.files.get("file")
    else:
        data = request.get_json(force=True) or {}
        prompt_text = (data.get("prompt") or data.get("topic") or "").strip()
        adaptive = bool(data.get("adaptive"))
        focus_context = data.get("focus_context") or []
        uploaded_file = None

    if not prompt_text:
        return jsonify({"error": "prompt is required"}), 400

    file_path: Optional[str] = None
    safe_filename: Optional[str] = None

    if uploaded_file and uploaded_file.filename:
        if not _allowed_file(uploaded_file.filename):
            return jsonify(
                {"error": f"File type not allowed. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}"}
            ), 400
        try:
            file_path, safe_filename = _save_upload(uploaded_file)
        except Exception as exc:
            return jsonify({"error": f"File upload failed: {exc}"}), 500

    source_info = detect_source(prompt=prompt_text, filename=safe_filename)
    source_type = source_info["source_type"]
    source_url = source_info.get("source_url")
    source_file_name = safe_filename or source_info.get("source_file_name")

    source_text: Optional[str] = None
    source_title: Optional[str] = None
    source_excerpt = ""

    try:
        if source_type == "website" and source_url:
            result = extract_webpage_safe(source_url)
            source_text = result.get("text") or ""
            source_title = result.get("title") or ""
            source_url = result.get("url") or normalize_url(source_url)
            source_excerpt = source_text[:300]

            if not source_text.strip():
                raise ValueError(
                    "Could not extract readable content from the page. "
                    "The page may be JavaScript-heavy, blocked, or require login. "
                    "Try using the direct article URL or paste the article text directly."
                )

        elif source_type == "youtube" and source_url:
            result = extract_youtube_transcript(source_url)
            source_text = result["text"]
            source_title = result["title"]
            source_excerpt = source_text[:300]

        elif source_type == "document" and file_path:
            result = extract_document(file_path, safe_filename or "document")
            source_text = result["text"]
            source_title = result["title"]
            source_excerpt = source_text[:300]

        elif source_type == "image" and file_path:
            mime = get_mime_type(safe_filename or "image.jpg")
            source_text = extract_image_with_gemini(
                file_path, mime, GEMINI_API_KEY, GEMINI_MODEL
            )
            source_title = safe_filename or "Uploaded Image"
            source_excerpt = source_text[:300]

    except Exception as exc:
        app.logger.exception("Source extraction error: %s", exc)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        return jsonify({"error": str(exc)}), 422

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    if source_text and source_title and source_type != "topic":
        topic_label: Optional[str] = source_title[:80]
    else:
        topic_label = None

    try:
        spec = parse_quiz_request(prompt_text)
        if topic_label and source_type != "topic":
            spec["topic"] = topic_label

        difficulty_plan = build_difficulty_plan(
            topic=spec["topic"],
            quiz_size=spec["quiz_size"],
            requested_difficulties=spec["requested_difficulties"],
            adaptive=adaptive,
            explicit_difficulty_counts=spec.get("explicit_difficulty_counts"),
        )

        spec["adaptive"] = adaptive
        spec["difficulty_plan"] = difficulty_plan
        spec["primary_level"] = primary_level_from_plan(difficulty_plan)

        questions, weak = generate_questions(
            spec,
            focus_context,
            source_text=source_text,
            source_title=source_title,
            source_type=source_type,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Quiz generation error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    session_id = uuid.uuid4().hex
    return jsonify(
        {
            "questions": questions,
            "weak_concepts": weak,
            "is_adaptive": adaptive or len(weak) > 0,
            "session_id": session_id,
            "source": {
                "type": source_type,
                "title": source_title or "",
                "url": source_url or "",
                "file_name": source_file_name or "",
                "excerpt": source_excerpt[:200],
            },
            "request": {
                "topic": spec["topic"],
                "source_prompt": spec["source_prompt"],
                "primary_level": spec["primary_level"],
                "difficulty_plan": spec["difficulty_plan"],
                "question_types": spec["question_types"],
                "quiz_size": spec["quiz_size"],
                "source_type": source_type,
                "source_title": source_title or "",
                "source_url": source_url or "",
                "source_file_name": source_file_name or "",
                "source_excerpt": source_excerpt[:200],
            },
        }
    )


@app.route("/submit-answers", methods=["POST"])
def submit_answers() -> Any:
    data = request.get_json(force=True) or {}
    answers = data.get("answers") or []
    session_meta = data.get("session_meta") or {}

    if not answers:
        return jsonify({"error": "answers are required"}), 400

    now = utcnow_iso()
    session_id = session_meta.get("session_id") or uuid.uuid4().hex
    topic = session_meta.get("topic") or answers[0].get("topic") or "Unknown"
    source_prompt = session_meta.get("source_prompt") or ""
    requested_types = session_meta.get("question_types") or ALL_QUESTION_TYPES
    difficulty_plan = session_meta.get("difficulty_plan") or []
    primary_level = session_meta.get("primary_level") or primary_level_from_plan(difficulty_plan)
    is_adaptive = 1 if session_meta.get("is_adaptive") else 0

    source_type = session_meta.get("source_type") or "topic"
    source_title = session_meta.get("source_title") or ""
    source_url = session_meta.get("source_url") or ""
    source_file_name = session_meta.get("source_file_name") or ""
    source_excerpt = session_meta.get("source_excerpt") or ""

    results = []
    incorrect_items = []

    with get_db() as conn:
        for idx, item in enumerate(answers):
            user_answer = str(item.get("user_answer", "")).strip()
            is_correct = evaluate_answer(item, user_answer)
            question_difficulty = item.get("difficulty") or (
                difficulty_plan[idx] if idx < len(difficulty_plan) else "easy"
            )

            conn.execute(
                """
                INSERT INTO quiz_attempts (
                    session_id, topic, concept, question_text, question_type,
                    difficulty, correct_answer, user_answer, is_correct, source_prompt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    item.get("topic", topic),
                    item.get("concept", "General"),
                    item.get("question", ""),
                    item.get("question_type", "mcq"),
                    question_difficulty,
                    item.get("correct_answer", ""),
                    user_answer,
                    1 if is_correct else 0,
                    source_prompt,
                    now,
                ),
            )

            result = {
                "question": item.get("question", ""),
                "user_answer": user_answer,
                "correct_answer": item.get("correct_answer", ""),
                "is_correct": is_correct,
                "explanation": item.get("explanation", ""),
                "concept": item.get("concept", "General"),
                "difficulty": question_difficulty,
                "question_type": item.get("question_type", "mcq"),
            }
            results.append(result)
            if not is_correct:
                incorrect_items.append(result)

        correct_count = sum(1 for item in results if item["is_correct"])
        total_questions = len(results)
        accuracy = round((correct_count / total_questions) * 100, 1) if total_questions else 0.0
        xp_earned = calculate_xp(results, bool(is_adaptive))

        conn.execute(
            """
            INSERT OR REPLACE INTO quiz_sessions (
                session_id, topic, source_prompt, requested_types, difficulty_plan, quiz_size,
                primary_level, is_adaptive, correct_count, total_questions, accuracy, xp_earned,
                source_type, source_title, source_url, source_file_name, source_excerpt, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                topic,
                source_prompt,
                json.dumps(requested_types),
                json.dumps(difficulty_plan),
                total_questions,
                primary_level,
                is_adaptive,
                correct_count,
                total_questions,
                accuracy,
                xp_earned,
                source_type,
                source_title,
                source_url,
                source_file_name,
                source_excerpt,
                now,
            ),
        )
        conn.commit()

    return jsonify(
        {
            "results": results,
            "score": {
                "correct": correct_count,
                "total": total_questions,
                "pct": round((correct_count / total_questions) * 100, 1) if total_questions else 0,
            },
            "xp_earned": xp_earned,
            "incorrect_items": incorrect_items,
            "session": {
                "session_id": session_id,
                "topic": topic,
                "primary_level": primary_level,
                "is_adaptive": bool(is_adaptive),
                "difficulty_plan": difficulty_plan,
            },
            "analytics": get_analytics_snapshot(),
        }
    )


@app.route("/analytics", methods=["GET"])
def analytics() -> Any:
    return jsonify(get_analytics_snapshot())


@app.route("/clear-history", methods=["POST"])
def clear_history() -> Any:
    with get_db() as conn:
        conn.execute("DELETE FROM quiz_attempts")
        conn.execute("DELETE FROM quiz_sessions")
        conn.commit()
    return jsonify({"ok": True, "message": "Quiz history cleared."})


@app.route("/weak-areas", methods=["GET"])
def weak_areas() -> Any:
    topic = (request.args.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic query param is required"}), 400
    return jsonify({"topic": topic, "weak_concepts": get_weak_concepts(topic)})


@app.route("/history", methods=["GET"])
def history() -> Any:
    return jsonify(get_topic_stats(limit=50))


if __name__ == "__main__":
    init_db()
    print("=== AdaptIQ — Adaptive AI Quiz System ===")
    print(f"Gemini SDK available: {GEMINI_AVAILABLE}")
    print(f"API key set:          {bool(GEMINI_API_KEY)}")
    print(f"Model:                {GEMINI_MODEL}")
    print(f"Database path:        {DATABASE_PATH}")
    print(f"Upload folder:        {UPLOAD_FOLDER}")
    print("Visit http://127.0.0.1:5000")
    app.run(debug=True, port=5000)