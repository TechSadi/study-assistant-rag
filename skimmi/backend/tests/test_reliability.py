"""Failures are handled cleanly: outages give a clear 503 and leave no half-saved
data, the vector index can be rebuilt from storage, and limits are enforced."""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import config, database, rag, storage, vectors
from app.main import app, recover_index

from .conftest import USER_A, USER_B, auth_headers, make_pdf, stored_path, upload


def nothing_left_behind(user_id=USER_A):
    assert database.list_documents(user_id) == []
    assert rag.chunk_count() == 0
    assert not config.UPLOAD_DIR.exists() or not any(p.is_file() for p in config.UPLOAD_DIR.rglob("*"))


# --- Uploads ----------------------------------------------------------------------

def test_storage_outage_during_upload_is_a_503_and_cleans_up(client, monkeypatch):
    def down(*args, **kwargs):
        raise storage.StorageError()

    monkeypatch.setattr(storage.LocalStorage, "put", down)
    res = upload(client, "a.pdf", make_pdf(["Alpha"]))
    assert res.status_code == 503
    assert res.json() == {"detail": "File storage is unavailable right now. Try again in a moment.",
                          "code": "unavailable"}
    nothing_left_behind()


def test_indexing_failure_during_upload_cleans_up(client, monkeypatch):
    def broken(*args, **kwargs):
        raise vectors.VectorStoreError()

    monkeypatch.setattr(vectors, "add", broken)
    res = upload(client, "a.pdf", make_pdf(["Alpha"]))
    assert res.status_code == 503 and "Search is unavailable" in res.json()["detail"]
    nothing_left_behind()


def test_unreadable_file_is_removed_from_storage(client):
    assert upload(client, "scan.pdf", make_pdf([""])).status_code == 422
    nothing_left_behind()


def test_files_are_stored_under_the_owners_folder(client):
    doc = upload(client, "My Notes (final).pdf", make_pdf(["Alpha"])).json()
    assert stored_path(USER_A, doc["id"]).read_bytes().startswith(b"%PDF-")
    row = database.get_document(USER_A, doc["id"])
    assert row["storage_path"] == f"{USER_A}/{doc['id']}.pdf"  # the user's file name is never in the path
    assert (doc["status"], row["size_bytes"] > 0) == ("ready", True)


def test_path_tricks_in_the_file_name_are_harmless(client):
    res = upload(client, "../../etc/passwd.pdf", make_pdf(["Alpha"]))
    assert res.status_code == 201 and res.json()["filename"] == "passwd.pdf"
    with pytest.raises(ValueError):
        storage.object_path(USER_A, "../x", ".pdf")
    with pytest.raises(ValueError):
        storage.LocalStorage(config.UPLOAD_DIR)._file("../outside.pdf")


def test_empty_and_oversized_files_are_rejected(client, monkeypatch):
    res = client.post("/api/documents", files={"file": ("empty.txt", b"", "text/plain")})
    assert res.status_code == 400 and "empty" in res.json()["detail"]

    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    big = b"%PDF-" + b"0" * (1024 * 1024)
    res = upload(client, "big.pdf", big)
    assert res.status_code == 413
    nothing_left_behind()


def test_oversized_request_bodies_are_refused_before_reading(client):
    res = client.post("/api/chat", content=b"{}", headers={"Content-Length": str(10 * 1024 * 1024),
                                                           "Content-Type": "application/json"})
    assert res.status_code == 413


def test_document_limit_per_user(client, other_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_DOCUMENTS_PER_USER", 1)
    assert upload(client, "a.pdf", make_pdf(["Alpha"])).status_code == 201
    res = upload(client, "b.pdf", make_pdf(["Beta"]))
    assert res.status_code == 409 and "up to 1 documents" in res.json()["detail"]
    assert upload(other_client, "b.pdf", make_pdf(["Beta"])).status_code == 201  # per user


def test_download_the_original_file(client):
    data = make_pdf(["Alpha"])
    doc = upload(client, "notes.pdf", data).json()
    res = client.get(f"/api/documents/{doc['id']}/file")
    assert res.status_code == 200 and res.content == data
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="notes.pdf"' in res.headers["content-disposition"]


def test_delete_removes_chunks_file_and_row_and_can_be_retried(client, monkeypatch):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    real_delete = storage.LocalStorage.delete
    outage = {"on": True}

    def flaky_delete(self, path):
        if outage["on"]:
            raise storage.StorageError()
        real_delete(self, path)

    monkeypatch.setattr(storage.LocalStorage, "delete", flaky_delete)
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 503
    assert len(client.get("/api/documents").json()) == 1  # still listed, so it can be deleted again

    outage["on"] = False
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    nothing_left_behind()


# --- Chat -------------------------------------------------------------------------

def test_document_still_processing_or_failed_is_explained(client):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    database.mark_document_processing(USER_A, doc["id"])
    res = client.post("/api/chat", json={"question": "Q", "doc_id": doc["id"]})
    assert res.status_code == 409 and "still being indexed" in res.json()["detail"]

    database.mark_document_failed(doc["id"], "The original file is missing.")
    res = client.post("/api/chat", json={"question": "Q", "doc_id": doc["id"]})
    assert res.status_code == 409 and "couldn't be indexed" in res.json()["detail"]


def test_ai_timeout_is_a_503_and_saves_nothing(client, monkeypatch):
    upload(client, "a.pdf", make_pdf(["Alpha"]))

    def slow(prompt, json_output=False):
        raise rag.GenerationError("The AI service took too long to answer. Try again.")

    monkeypatch.setattr(rag, "call_gemini", slow)
    res = client.post("/api/chat", json={"question": "Q"})
    assert res.status_code == 503 and "too long" in res.json()["detail"]
    assert client.get("/api/conversations").json() == []


def test_gemini_timeout_is_not_retried_on_every_fallback(monkeypatch):
    import httpx

    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs["model"])
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(rag, "get_llm", lambda: type("C", (), {"models": Models()})())
    with pytest.raises(rag.GenerationError, match="too long"):
        rag.call_gemini("prompt")
    assert len(calls) == 1


def test_search_outage_during_chat_is_a_503(client, monkeypatch):
    upload(client, "a.pdf", make_pdf(["Alpha"]))

    def broken(*args, **kwargs):
        raise vectors.VectorStoreError()

    monkeypatch.setattr(vectors, "query", broken)
    res = client.post("/api/chat", json={"question": "Q"})
    assert res.status_code == 503 and "Search is unavailable" in res.json()["detail"]


def test_database_outage_is_a_503_without_details(client, monkeypatch):
    def down():
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("password=hunter2 host=db"))

    monkeypatch.setattr(database, "begin", down)
    res = TestClient(app, raise_server_exceptions=False, headers=auth_headers()).get("/api/documents")
    assert res.status_code == 503
    assert "database" in res.json()["detail"] and "hunter2" not in res.text


def test_unexpected_errors_hide_internals(client, monkeypatch):
    def bug(user_id):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(database, "list_conversations", bug)
    res = TestClient(app, raise_server_exceptions=False, headers=auth_headers()).get("/api/conversations")
    assert res.status_code == 500
    assert res.json() == {"detail": "Something went wrong on the server. Try again.", "code": "server_error"}


def test_validation_errors_have_the_same_shape(client):
    res = client.post("/api/chat", json={"question": "Q", "mode": "essay"})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "invalid_request" and body["detail"].startswith("mode:")


def test_chat_is_rate_limited_per_user(client, other_client, monkeypatch):
    monkeypatch.setitem(config.RATE_LIMITS, "chat", (2, 60))
    upload(client, "a.pdf", make_pdf(["Alpha"]))
    upload(other_client, "b.pdf", make_pdf(["Beta"]))
    codes = [client.post("/api/chat", json={"question": "Q"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert other_client.post("/api/chat", json={"question": "Q"}).status_code == 200


def test_cors_only_allows_configured_origins(client):
    allowed = config.CORS_ORIGINS[0]
    res = client.options("/api/documents", headers={"Origin": allowed, "Access-Control-Request-Method": "GET"})
    assert res.headers.get("access-control-allow-origin") == allowed
    res = client.options("/api/documents", headers={"Origin": "https://evil.example",
                                                     "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in res.headers


def test_security_headers(client):
    res = client.get("/api/documents")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["Cache-Control"] == "no-store"


# --- Index recovery ----------------------------------------------------------------

def test_lost_vector_index_is_rebuilt_from_storage(client, other_client):
    a = upload(client, "a.pdf", make_pdf(["Alpha text", "More alpha"])).json()
    b = upload(other_client, "b.pdf", make_pdf(["Beta text"])).json()

    # A fresh disk: the vector index is empty.
    collection = vectors.get_collection()
    collection.delete(ids=collection.get(include=[])["ids"])
    assert rag.chunk_count() == 0

    recover_index()
    assert rag.document_chunk_count(a["id"]) == 2 and rag.document_chunk_count(b["id"]) == 1
    # Ownership survives the rebuild.
    assert rag.scope_size(rag.scope_filter(USER_A)) == 2
    assert rag.scope_size(rag.scope_filter(USER_B)) == 1
    assert client.post("/api/chat", json={"question": "alpha"}).status_code == 200
    assert database.get_document(USER_A, a["id"])["status"] == "ready"


def test_upload_interrupted_before_indexing_is_finished_at_startup(client, monkeypatch):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    # As if the server stopped after storing the file but before indexing it.
    rag.remove_document(doc["id"])
    with database.begin() as conn:
        conn.execute(database.documents.update().values(status="processing", index_version=0))

    recover_index()
    assert database.get_document(USER_A, doc["id"])["status"] == "ready"
    assert rag.document_chunk_count(doc["id"]) == 1


def test_recovery_skips_documents_that_are_fine(client, monkeypatch):
    upload(client, "a.pdf", make_pdf(["Alpha"]))
    calls = []
    monkeypatch.setattr(rag, "reindex_document", lambda *a: calls.append(a))
    recover_index()
    assert calls == []


def test_recovery_leaves_documents_for_later_when_storage_is_down(client, monkeypatch):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    rag.remove_document(doc["id"])
    monkeypatch.setattr(storage.LocalStorage, "get", lambda self, p: (_ for _ in ()).throw(storage.StorageError()))
    recover_index()
    assert database.get_document(USER_A, doc["id"])["status"] == "processing"  # tried again next start


def test_orphaned_chunks_are_removed(client):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    rag.index_chunks("99999999-9999-4999-8999-999999999999", [{"text": "x", "source": "x.pdf", "page": 1}], USER_B)
    recover_index()
    assert rag.chunk_count() == 1 and rag.document_chunk_count(doc["id"]) == 1


def test_failed_document_can_be_reindexed_on_demand(client):
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    rag.remove_document(doc["id"])
    database.mark_document_failed(doc["id"], "Indexing failed.")

    res = client.post(f"/api/documents/{doc['id']}/reindex")
    assert res.status_code == 200 and res.json()["status"] == "ready"
    assert rag.document_chunk_count(doc["id"]) == 1

    stored_path(USER_A, doc["id"]).unlink()
    res = client.post(f"/api/documents/{doc['id']}/reindex")
    assert res.status_code == 410
    assert client.get("/api/documents").json()[0]["status"] == "failed"
