"""Cross-user access: USER_B must never see or change USER_A's data, whatever ids
they put in the URL, body or query. Every case answers exactly as if the item
didn't exist, so ids can't even be probed."""

import json

from app import rag

from .conftest import USER_A, USER_B, make_pdf, stored_path, upload


def setup_user_a(client):
    """USER_A's document (with a subject), conversation, quiz and quiz attempt."""
    doc = upload(client, "secret.pdf", make_pdf(["Alpha secret text", "More alpha"])).json()
    client.patch(f"/api/documents/{doc['id']}", json={"subject": "Private"})
    quiz = client.post("/api/chat", json={"mode": "quiz", "doc_id": doc["id"]}).json()
    client.post(f"/api/messages/{quiz['message']['id']}/quiz-attempts", json={"answers": [1, 0]})
    return doc, quiz


def test_documents_are_private(client, other_client):
    doc, _ = setup_user_a(client)
    url = f"/api/documents/{doc['id']}"

    assert other_client.get("/api/documents").json() == []
    assert other_client.patch(url, json={"subject": "Stolen"}).status_code == 404
    assert other_client.delete(url).status_code == 404
    assert other_client.post(f"{url}/reindex").status_code == 404
    assert other_client.get(f"{url}/file").status_code == 404

    # Nothing changed for the owner.
    mine = client.get("/api/documents").json()
    assert [(d["id"], d["subject"]) for d in mine] == [(doc["id"], "Private")]
    assert rag.document_chunk_count(doc["id"]) == 2
    assert stored_path(USER_A, doc["id"]).exists()
    assert client.get(f"{url}/file").content.startswith(b"%PDF-")


def test_chat_cannot_reach_another_users_document_or_subject(client, other_client, prompts):
    doc, _ = setup_user_a(client)
    upload(other_client, "mine.pdf", make_pdf(["Beta own text"]))

    res = other_client.post("/api/chat", json={"question": "Tell me", "doc_id": doc["id"]})
    assert res.status_code == 404
    res = other_client.post("/api/chat", json={"question": "Tell me", "subject": "Private"})
    assert res.status_code == 404
    res = other_client.post("/api/chat", json={"mode": "summarize", "doc_id": doc["id"], "page_from": 1})
    assert res.status_code == 404

    # Searching "all documents" only searches USER_B's own.
    for mode in ("ask", "summarize", "quiz", "flashcards"):
        body = other_client.post("/api/chat", json={"question": "alpha secret", "mode": mode}).json()
        assert {s["source"] for s in body["message"]["sources"]} == {"mine.pdf"}
        assert "Alpha secret" not in prompts[-1]


def test_user_without_documents_cannot_search_other_users_chunks(client, other_client):
    setup_user_a(client)
    res = other_client.post("/api/chat", json={"question": "alpha"})
    assert res.status_code == 400 and "Upload a document" in res.json()["detail"]


def test_retrieval_never_crosses_users_even_with_a_foreign_doc_id(client, other_client):
    doc, _ = setup_user_a(client)
    upload(other_client, "mine.pdf", make_pdf(["Beta own text"]))
    # Even a filter naming USER_A's document returns nothing when scoped to USER_B.
    where = rag.scope_filter(USER_B, doc["id"])
    assert rag.scope_size(where) == 0
    assert rag.retrieve_context("alpha", where=where) == []
    assert all(c["doc_id"] != doc["id"] for c in rag.scope_chunks(rag.scope_filter(USER_B)))


def test_unscoped_retrieval_is_refused():
    import pytest

    for where in (None, {"doc_id": "d1"}, {"$and": [{"doc_id": "d1"}, {"page": 1}]}):
        with pytest.raises(ValueError):
            rag.scope_size(where)
    with pytest.raises(ValueError):
        rag.scope_filter(None)


def test_conversations_are_private(client, other_client):
    _, quiz = setup_user_a(client)
    cid = quiz["conversation"]["id"]
    upload(other_client, "mine.pdf", make_pdf(["Beta"]))

    assert other_client.get("/api/conversations").json() == []
    assert other_client.get(f"/api/conversations/{cid}").status_code == 404
    assert other_client.delete(f"/api/conversations/{cid}").status_code == 404
    # Continuing someone else's conversation is refused (and would leak its history).
    res = other_client.post("/api/chat", json={"question": "And then?", "conversation_id": cid})
    assert res.status_code == 404

    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 2


def test_quizzes_and_progress_are_private(client, other_client):
    _, quiz = setup_user_a(client)
    message_id = quiz["message"]["id"]

    res = other_client.post(f"/api/messages/{message_id}/quiz-attempts", json={"answers": [1, 0]})
    assert res.status_code == 404
    progress = other_client.get("/api/progress").json()
    assert (progress["quizzes_completed"], progress["recent"], progress["activity"]["quiz"]) == (0, [], 0)
    assert client.get("/api/progress").json()["quizzes_completed"] == 1


def test_account_counts_are_per_user(client, other_client):
    setup_user_a(client)
    assert other_client.get("/api/me").json()["documents"] == 0
    assert client.get("/api/me").json()["documents"] == 1


def test_users_can_upload_the_same_file_and_names(client, other_client):
    data = make_pdf(["Shared lecture notes"])
    a = upload(client, "notes.pdf", data)
    b = upload(other_client, "notes.pdf", data)
    assert a.status_code == b.status_code == 201
    a, b = a.json(), b.json()
    assert a["id"] != b["id"]
    assert a["filename"] == b["filename"] == "notes.pdf"  # names are unique per user only
    assert stored_path(USER_A, a["id"]).exists() and stored_path(USER_B, b["id"]).exists()

    # Deleting one user's copy leaves the other's intact.
    client.delete(f"/api/documents/{a['id']}")
    assert rag.document_chunk_count(b["id"]) == 1
    assert [d["id"] for d in other_client.get("/api/documents").json()] == [b["id"]]


def test_subject_spelling_is_not_shared_between_users(client, other_client):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    client.patch(f"/api/documents/{doc['id']}", json={"subject": "BIOLOGY"})
    mine = upload(other_client, "b.pdf", make_pdf(["Beta"])).json()
    res = other_client.patch(f"/api/documents/{mine['id']}", json={"subject": "biology"})
    assert res.json()["subject"] == "biology"


def test_malformed_ids_are_not_found(client):
    for path in ("/api/documents/nope", "/api/conversations/'; DROP TABLE documents;--"):
        assert client.delete(path).status_code == 404
    assert client.get("/api/conversations/nope").status_code == 404
    assert client.post("/api/chat", json={"question": "Q", "doc_id": "nope"}).status_code in (400, 404)


def test_new_quiz_request_keeps_only_the_owners_scope(client, other_client):
    doc, quiz = setup_user_a(client)
    covered = json.loads(quiz["message"]["content"])["request"]
    assert covered["doc_id"] == doc["id"]
    # Replaying the saved request as another user fails like any foreign id.
    upload(other_client, "b.pdf", make_pdf(["Beta"]))
    assert other_client.post("/api/chat", json={"mode": "quiz", **covered}).status_code == 404
