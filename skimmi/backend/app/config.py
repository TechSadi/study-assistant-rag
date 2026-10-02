"""Settings for the Skimmi AI backend, read from environment variables."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Secrets and overrides live in skimmi/backend/.env (see .env.example).
# Variables already set in the environment take precedence.
load_dotenv(BACKEND_DIR / ".env")


def _flag(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Where things are stored:
#   PostgreSQL (DATABASE_URL)  users' documents metadata, conversations, quiz results
#   Supabase Storage (private) the original uploaded files, at <user id>/<document id>.<ext>
#   ChromaDB (CHROMA_DIR)      document chunks + embeddings. Treated as a cache: it can
#                              be rebuilt from the stored files (see main.recover_index).
# Local files live under one data directory. A relative SKIMMI_DATA_DIR is resolved
# from skimmi/backend, not the current directory.
DATA_DIR = BACKEND_DIR / os.getenv("SKIMMI_DATA_DIR", "data")
UPLOAD_DIR = DATA_DIR / "uploads"          # the "local" storage backend (tests, offline dev)
CHROMA_DIR = DATA_DIR / "chroma_db"
LEGACY_DATABASE_FILE = DATA_DIR / "skimmi.db"  # the pre-auth SQLite database (import_legacy.py)

# Supabase PostgreSQL in production, e.g.
#   postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
# Without it, a local SQLite file is used (development and tests only).
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'skimmi-dev.db').as_posix()}"
# Apply pending Alembic migrations when the backend starts.
AUTO_MIGRATE = _flag("SKIMMI_AUTO_MIGRATE", True)

# --- Supabase (auth + storage) ---
# The project URL, e.g. https://abcd1234.supabase.co. The Data API URL
# (…/rest/v1) is accepted too: the suffix is removed.
SUPABASE_URL = re.sub(r"/rest/v1/?$", "", os.getenv("SUPABASE_URL", "").strip()).rstrip("/")
# Only for projects still on the legacy shared JWT secret (HS256). Projects using
# the newer signing keys are verified with the public keys (JWKS) instead.
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
JWT_AUDIENCE = "authenticated"
# The service role key bypasses Row Level Security. It is read only here, on the
# backend, and used only for Storage. It must never be given to the frontend.
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BACKEND = os.getenv("SKIMMI_STORAGE_BACKEND", "supabase")  # "supabase" or "local"
STORAGE_BUCKET = os.getenv("SKIMMI_STORAGE_BUCKET", "documents")

# Seconds to wait for Supabase (storage, signing keys) and Gemini before giving up.
HTTP_TIMEOUT = float(os.getenv("SKIMMI_HTTP_TIMEOUT", "30"))
GEMINI_TIMEOUT = float(os.getenv("SKIMMI_GEMINI_TIMEOUT", "60"))

# Rebuild missing or outdated vector indexes from the stored files at startup,
# in a background thread (see main.recover_index).
RECOVER_ON_STARTUP = _flag("SKIMMI_RECOVER_ON_STARTUP", True)

COLLECTION_NAME = "skimmi_documents"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# Tried in order when GEMINI_MODEL is overloaded or out of quota (comma-separated).
# Each model has its own free-tier daily quota, so a few fallbacks keep the app
# working after the main model's quota runs out. The lite models come last.
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
    ).split(",")
    if m.strip() and m.strip() != GEMINI_MODEL
]

# Chunks are built from whole paragraphs and sentences, up to CHUNK_SIZE characters,
# and repeat up to CHUNK_OVERLAP characters of whole sentences from the chunk before.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
# Bump when extraction, chunking or chunk metadata changes: documents indexed with
# an older version are re-indexed from their stored file when the backend starts.
# 3: chunks carry the owner's user_id.
INDEX_VERSION = 3
# Files without real pages (text, Markdown, Word files without saved page breaks)
# are cut into "pages" of about this many characters, so citations still point
# somewhere specific. Roughly one printed page.
TEXT_PAGE_CHARS = 3000

TOP_K = 4
# A retrieved chunk is dropped if it is this much further (cosine distance) from the
# question than the best match, so weak matches don't dilute the context.
RELEVANCE_MARGIN = 0.15
EXCERPT_CHARS = 320    # length of the source excerpt shown under an answer

# Study modes. "ask" is the plain question answering from Phase 1.
MODES = ("ask", "explain", "summarize", "quiz", "flashcards")
EXPLAIN_TOP_K = 5      # explanations get a little more material than a direct answer
STUDY_CHUNKS = 16      # most chunks sent for a summary, quiz or flashcard set (~13k chars)
QUIZ_MAX_QUESTIONS = 10
QUIZ_DEFAULT_QUESTIONS = 5
FLASHCARDS_MAX = 15

# How much of the conversation is sent with a follow-up question.
HISTORY_MESSAGES = 6          # the last 3 question/answer pairs
HISTORY_MESSAGE_CHARS = 1200  # long answers are trimmed to this length in the prompt

SUBJECT_MAX_CHARS = 60  # a document's subject and course names
RECENT_QUIZZES = 20     # quiz attempts listed in the progress summary

MAX_UPLOAD_MB = int(os.getenv("SKIMMI_MAX_UPLOAD_MB", "25"))
MAX_DOCUMENTS_PER_USER = int(os.getenv("SKIMMI_MAX_DOCUMENTS", "100"))
MAX_JSON_BODY_BYTES = 256 * 1024  # every request except uploads

# Requests allowed per user (or per IP for failed sign-ins) in a sliding window.
# Kept in memory, so the limits are per backend process.
RATE_LIMITS = {
    "chat": (int(os.getenv("SKIMMI_CHAT_PER_MINUTE", "15")), 60),
    "upload": (int(os.getenv("SKIMMI_UPLOADS_PER_HOUR", "30")), 3600),
    "write": (120, 60),       # other changes: subjects, deletions, quiz attempts
    "auth_failure": (30, 300),  # requests with a missing or invalid token, per IP
}

# Origins allowed to call the API directly (the Vite dev server proxies /api,
# so this only matters if the frontend is served from somewhere else).
# Set to the deployed frontend's origin in production. Never "*".
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("SKIMMI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip() and o.strip() != "*"
]


def get_gemini_api_key():
    return os.getenv("GEMINI_API_KEY")
