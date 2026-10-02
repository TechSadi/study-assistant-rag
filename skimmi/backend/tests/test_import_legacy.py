"""Moving the single-user data of the pre-authentication version into an account."""

import json
import sqlite3

from app import rag
from app.import_legacy import import_legacy

from .conftest import USER_A, USER_B, make_pdf, stored_path

LEGACY_SCHEMA = """
CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, pages INTEGER, chunks INTEGER,
    uploaded_at TEXT, subject TEXT DEFAULT '', course TEXT DEFAULT '', index_version INTEGER DEFAULT 1);
CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, content TEXT,
    sources TEXT DEFAULT '[]', created_at TEXT, mode TEXT DEFAULT 'ask', scope TEXT DEFAULT '');
CREATE TABLE quiz_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, title TEXT,
    scope TEXT DEFAULT '', correct INTEGER, total INTEGER, answers TEXT DEFAULT '[]', completed_at TEXT);
"""
DOC_ID = "33333333-3333-4333-8333-333333333333"
CHAT_ID = "44444444-4444-4444-8444-444444444444"


def make_legacy(tmp_path):
    uploads = tmp_path / "legacy_uploads"
    uploads.mkdir()
    (uploads / f"{DOC_ID}.pdf").write_bytes(make_pdf(["Old alpha notes", "Page two"]))
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    t = "2026-09-01T10:00:00+00:00"
    conn.execute("INSERT INTO documents VALUES (?, 'old.pdf', 'h1', 2, 2, ?, 'Biology', 'BIO 1', 2)", (DOC_ID, t))
    conn.execute("INSERT INTO documents VALUES ('55555555-5555-4555-8555-555555555555', 'gone.pdf', 'h2', 1, 1, ?, '', '', 2)", (t,))
    conn.execute("INSERT INTO conversations VALUES (?, 'Old chat', ?, ?)", (CHAT_ID, t, t))
    quiz = json.dumps({"questions": [{"type": "multiple_choice", "question": "Q", "options": ["a", "b"], "answer": "a"}]})
    conn.execute("INSERT INTO messages (conversation_id, role, content, created_at, mode) VALUES (?, 'user', 'Quiz me', ?, 'quiz')", (CHAT_ID, t))
    conn.execute("INSERT INTO messages (conversation_id, role, content, sources, created_at, mode) VALUES (?, 'assistant', ?, ?, ?, 'quiz')",
                 (CHAT_ID, quiz, json.dumps([{"doc_id": DOC_ID, "source": "old.pdf", "page": 1, "excerpt": "x"}]), t))
    conn.execute("INSERT INTO quiz_attempts (message_id, title, correct, total, answers, completed_at) VALUES (2, 'Quiz me', 1, 1, '[0]', ?)", (t,))
    conn.commit()
    conn.close()
    return db, uploads


def test_import_legacy_data(client, other_client, env):
    db, uploads = make_legacy(env)
    counts = import_legacy(USER_A, "a@example.com", db, uploads)
    assert counts == {"documents": 1, "conversations": 1, "messages": 2, "quiz_attempts": 1}

    docs = client.get("/api/documents").json()
    assert [(d["id"], d["filename"], d["subject"], d["status"]) for d in docs] == [(DOC_ID, "old.pdf", "Biology", "ready")]
    assert stored_path(USER_A, DOC_ID).exists()
    assert rag.scope_size(rag.scope_filter(USER_A, DOC_ID)) == 2  # re-indexed with its owner

    chat = client.get(f"/api/conversations/{CHAT_ID}").json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["last_attempt"]["correct"] == 1
    assert client.get("/api/progress").json()["quizzes_completed"] == 1
    assert client.post("/api/chat", json={"question": "alpha", "conversation_id": CHAT_ID}).status_code == 200

    # Only the named user gets it, and running again changes nothing.
    assert other_client.get("/api/documents").json() == []
    assert import_legacy(USER_A, "", db, uploads) == {"documents": 0, "conversations": 0, "messages": 0, "quiz_attempts": 0}
    assert import_legacy(USER_B, "", db, uploads)["documents"] == 0  # already given to USER_A
    assert other_client.get("/api/documents").json() == []
