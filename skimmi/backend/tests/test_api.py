from fastapi.testclient import TestClient

from app import config, rag, vectors
from app.main import app

from .conftest import USER_A, auth_headers, make_pdf, stored_path, upload


# --- Documents --------------------------------------------------------------

def test_upload_list_ask_and_delete(client):
    res = upload(client, "biology.pdf", make_pdf(["Cells are the unit of life.", "Mitochondria make ATP."]))
    assert res.status_code == 201
    doc = res.json()
    assert doc["filename"] == "biology.pdf"
    assert doc["pages"] == 2 and doc["chunks"] == 2
    assert len(doc["id"]) == 36  # a UUID

    assert [d["id"] for d in client.get("/api/documents").json()] == [doc["id"]]

    res = client.post("/api/chat", json={"question": "What makes ATP?"})
    assert res.status_code == 200
    sources = res.json()["message"]["sources"]
    assert {(s["doc_id"], s["source"], s["page"]) for s in sources} == {
        (doc["id"], "biology.pdf", 1),
        (doc["id"], "biology.pdf", 2),
    }

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get("/api/documents").json() == []
    assert rag.chunk_count() == 0
    assert not stored_path(USER_A, doc["id"]).exists()


def test_same_filename_different_content_is_kept_apart(client):
    first = upload(client, "notes.pdf", make_pdf(["Week one notes"])).json()
    second = upload(client, "Notes.pdf", make_pdf(["Week two notes", "More"])).json()

    assert first["filename"] == "notes.pdf"
    assert second["filename"] == "Notes (2).pdf"
    assert first["id"] != second["id"]
    assert rag.document_chunk_count(second["id"]) == 2

    # Deleting one removes only its own chunks and file.
    client.delete(f"/api/documents/{first['id']}")
    assert [d["id"] for d in client.get("/api/documents").json()] == [second["id"]]
    assert rag.document_chunk_count(first["id"]) == 0
    assert rag.document_chunk_count(second["id"]) == 2
    assert not stored_path(USER_A, first["id"]).exists()
    assert stored_path(USER_A, second["id"]).exists()


def test_rejects_the_same_file_twice(client):
    data = make_pdf(["Some notes"])
    upload(client, "notes.pdf", data)
    res = upload(client, "copy of notes.pdf", data)
    assert res.status_code == 409
    assert "notes.pdf" in res.json()["detail"]
    assert rag.chunk_count() == 1


def test_rejects_unsupported_files(client):
    res = client.post("/api/documents", files={"file": ("photo.png", b"\x89PNG", "image/png")})
    assert res.status_code == 400
    res = client.post("/api/documents", files={"file": ("old.doc", b"\xd0\xcf\x11\xe0", "application/msword")})
    assert res.status_code == 400 and ".docx" in res.json()["detail"]
    assert upload(client, "fake.pdf", b"not really a pdf").status_code == 400
    assert upload(client, "fake.docx", b"not a zip").status_code == 400
    assert upload(client, "binary.txt", b"\x00\x01\x02").status_code == 400
    assert client.get("/api/documents").json() == []


def test_rejects_pdf_without_text(client):
    assert upload(client, "scan.pdf", make_pdf([""])).status_code == 422
    assert client.get("/api/documents").json() == []
    assert not config.UPLOAD_DIR.exists() or not any(config.UPLOAD_DIR.rglob('*.pdf'))


def test_delete_unknown_document(client):
    assert client.delete("/api/documents/nope").status_code == 404


# --- Chat and conversations -------------------------------------------------

def test_chat_requires_documents(client):
    assert client.post("/api/chat", json={"question": "Anything?"}).status_code == 400


def test_first_question_starts_a_titled_conversation(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    body = client.post("/api/chat", json={"question": "What is alpha?"}).json()

    conversation = body["conversation"]
    assert conversation["title"] == "What is alpha?"
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "Stub answer 1."

    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [(m["role"], m["content"]) for m in saved["messages"]] == [
        ("user", "What is alpha?"),
        ("assistant", "Stub answer 1."),
    ]
    assert saved["messages"][1]["sources"][0]["source"] == "a.pdf"


def test_follow_up_sends_history_to_the_llm(client, prompts):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    first = client.post("/api/chat", json={"question": "What is alpha?"}).json()
    cid = first["conversation"]["id"]

    res = client.post("/api/chat", json={"question": "Explain it again", "conversation_id": cid})
    assert res.status_code == 200
    assert res.json()["conversation"]["id"] == cid

    assert "CONVERSATION HISTORY" not in prompts[0]
    assert "Student: What is alpha?" in prompts[1]
    assert "Assistant: Stub answer 1." in prompts[1]
    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 4


def test_conversations_are_separate(client, prompts):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    one = client.post("/api/chat", json={"question": "First topic"}).json()["conversation"]["id"]
    two = client.post("/api/chat", json={"question": "Second topic"}).json()["conversation"]["id"]
    assert one != two

    client.post("/api/chat", json={"question": "More", "conversation_id": two})
    assert "First topic" not in prompts[-1]

    listed = client.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [two, one]  # most recently used first


def test_delete_conversation(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    cid = client.post("/api/chat", json={"question": "Q"}).json()["conversation"]["id"]

    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.get("/api/conversations").json() == []
    assert client.delete(f"/api/conversations/{cid}").status_code == 404
    # Documents are untouched.
    assert len(client.get("/api/documents").json()) == 1


def test_unknown_conversation_is_rejected(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    res = client.post("/api/chat", json={"question": "Q", "conversation_id": "missing"})
    assert res.status_code == 404


def test_failed_answer_saves_nothing(client, monkeypatch):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))

    def fail(prompt):
        raise rag.GenerationError("Gemini is temporarily unavailable.")

    monkeypatch.setattr(rag, "call_gemini", fail)
    res = client.post("/api/chat", json={"question": "What is alpha?"})
    assert res.status_code == 503
    assert "temporarily unavailable" in res.json()["detail"]
    assert client.get("/api/conversations").json() == []


def test_data_survives_a_restart(client):
    doc = upload(client, "a.pdf", make_pdf(["Alpha text"])).json()
    cid = client.post("/api/chat", json={"question": "Q"}).json()["conversation"]["id"]

    # Simulate a backend restart: drop the cached ChromaDB client and use a new app client.
    vectors.get_collection.cache_clear()
    fresh = TestClient(app, headers=auth_headers(USER_A))

    assert [d["id"] for d in fresh.get("/api/documents").json()] == [doc["id"]]
    assert rag.document_chunk_count(doc["id"]) == 1
    assert len(fresh.get(f"/api/conversations/{cid}").json()["messages"]) == 2
