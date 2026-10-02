"""Test setup: everything is isolated and offline.

* A fresh SQLite database per test, created by the real Alembic migrations.
  With SKIMMI_TEST_ALL_ON_POSTGRES=1 and SKIMMI_TEST_POSTGRES_URL set, every test
  uses that (disposable!) PostgreSQL database instead, reset before each test.
* Local file storage and ChromaDB in a temporary directory.
* A fake embedder and a stubbed Gemini call.
* Supabase access tokens signed with a test secret (HS256), for two test users.
  `client` is signed in as USER_A, `other_client` as USER_B.
"""

import hashlib
import json
import os
import time

import jwt
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import auth, config, database, rag, ratelimit, storage, vectors
from app.main import app

SUPABASE_URL = "https://test-project.supabase.co"
JWT_SECRET = "test-only-secret-that-is-long-enough-for-hs256"
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


def make_token(user_id=USER_A, email=None, secret=JWT_SECRET, **claims):
    """A Supabase-style access token. Keyword arguments override claims (None removes one)."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email if email is not None else f"{user_id[:4]}@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_headers(user_id=USER_A, **claims):
    return {"Authorization": f"Bearer {make_token(user_id, **claims)}"}


def fake_embed(texts):
    """Deterministic 16-dim vectors so tests don't load the real model."""
    vectors_ = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        vec = [b / 255 for b in digest[:16]]
        norm = sum(v * v for v in vec) ** 0.5 or 1
        vectors_.append([v / norm for v in vec])
    return vectors_


STUB_QUIZ = {
    "questions": [
        {"type": "multiple_choice", "question": "What is alpha?", "options": ["A letter", "A fish", "A car", "A tree"],
         "answer": "B", "explanation": "Stub.", "source": "a.pdf", "page": 1},
        {"type": "multiple_choice", "question": "Which letter comes first?", "options": ["Alpha", "Beta"],
         "answer": "Alpha", "explanation": "", "source": "missing.pdf", "page": 99},
        {"type": "short_answer", "question": "Define alpha.", "answer": "The first letter."},
        {"type": "multiple_choice", "question": "Broken", "options": ["x", "y"], "answer": "z"},
        {"type": "multiple_choice", "question": "Twice", "options": ["x", "X"], "answer": "x"},
    ]
}
STUB_CARDS = {"cards": [{"front": "Alpha?", "back": "The first letter.", "page": 1}, {"front": "", "back": "x"}]}


@pytest.fixture
def prompts(monkeypatch):
    """Stubs Gemini. Every prompt sent is recorded in the returned list.
    JSON requests (quizzes, flashcards) get STUB_QUIZ or STUB_CARDS."""
    sent = []

    def fake_gemini(prompt, json_output=False):
        sent.append(prompt)
        if json_output:
            return json.dumps(STUB_QUIZ if '"questions"' in prompt else STUB_CARDS)
        return f"Stub answer {len(sent)}."

    monkeypatch.setattr(rag, "call_gemini", fake_gemini)
    return sent


def _clear_caches():
    for cached in (database.get_engine, storage.get_storage, vectors.get_collection, auth._jwks_client):
        getattr(cached, "cache_clear", lambda: None)()  # a test may have replaced it
    auth.forget_profiles()
    ratelimit.reset()


def _test_database_url(tmp_path):
    postgres = os.getenv("SKIMMI_TEST_POSTGRES_URL")
    if not (postgres and os.getenv("SKIMMI_TEST_ALL_ON_POSTGRES") == "1"):
        return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    import sqlalchemy as sa

    engine = sa.create_engine(database.database_url(postgres))
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public"))
    engine.dispose()
    return postgres


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated storage, database and auth settings, with the schema migrated."""
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma_db")
    monkeypatch.setattr(config, "DATABASE_URL", _test_database_url(tmp_path))
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(rag, "embed", fake_embed)
    # Fake embeddings have no meaning, so don't drop "less relevant" chunks by distance.
    monkeypatch.setattr(config, "RELEVANCE_MARGIN", 2.0)
    _clear_caches()
    database.migrate()
    yield tmp_path
    database.get_engine().dispose()
    _clear_caches()


@pytest.fixture
def client(env, prompts):
    """Signed in as USER_A. No `with` block: skips the lifespan, so the real
    embedding model isn't loaded."""
    return TestClient(app, headers=auth_headers(USER_A))


@pytest.fixture
def other_client(env, prompts):
    """A second user, USER_B, sharing the same backend."""
    return TestClient(app, headers=auth_headers(USER_B))


@pytest.fixture
def anonymous(env, prompts):
    return TestClient(app)


def stored_path(user_id, doc_id, extension=".pdf"):
    """Where the local storage backend keeps a document's file."""
    return config.UPLOAD_DIR / user_id / f"{doc_id}{extension}"


def make_pdf(pages):
    """Build an in-memory PDF with one string of text per page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def upload(client, name, data):
    return client.post("/api/documents", files={"file": (name, data, "application/pdf")})
