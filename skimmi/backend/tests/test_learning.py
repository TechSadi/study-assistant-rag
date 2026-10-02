"""Interactive quizzes (scoring and saved results), progress, subjects and courses,
subject-scoped retrieval, relevance trimming, source excerpts and re-indexing."""

import json

import pytest

from app import config, database, progress, rag

from .conftest import USER_A, make_pdf, stored_path, upload


def chat(client, **body):
    return client.post("/api/chat", json=body)


def make_quiz(client, **body):
    """A saved quiz from the stub: question 1's answer is option 1 ("A fish"),
    question 2's is option 0 ("Alpha")."""
    res = chat(client, mode="quiz", **body)
    assert res.status_code == 200
    return res.json()


# --- Scoring ------------------------------------------------------------------

QUESTIONS = [
    {"type": "multiple_choice", "question": "Q1", "options": ["a", "b", "c"], "answer": "b"},
    {"type": "multiple_choice", "question": "Q2", "options": ["x", "y"], "answer": "x"},
    {"type": "short_answer", "question": "Q3", "answer": "free text"},  # an older quiz
]


def test_score_quiz():
    score = progress.score_quiz(QUESTIONS, [1, 1, None])
    assert score == {"results": [True, False, None], "correct": 1, "total": 2}
    assert progress.score_quiz(QUESTIONS, [1, 0, None])["correct"] == 2


@pytest.mark.parametrize("answers, message", [
    ([1, 0], "Expected 3 answers"),
    ([1, None, None], "Question 2 needs an answer"),
    ([3, 0, None], "Question 1 needs an answer"),
    ([-1, 0, None], "Question 1 needs an answer"),
    ([True, 0, None], "Question 1 needs an answer"),
])
def test_score_quiz_rejects_bad_answers(answers, message):
    with pytest.raises(progress.ScoringError, match=message):
        progress.score_quiz(QUESTIONS, answers)


def test_score_quiz_needs_a_multiple_choice_question():
    with pytest.raises(progress.ScoringError, match="no multiple-choice"):
        progress.score_quiz(QUESTIONS[2:], [None])


def test_accuracy():
    assert progress.accuracy(0, 0) is None
    assert progress.accuracy(3, 4) == 0.75
    assert progress.accuracy(2, 3) == 0.6667


# --- Quiz attempts and progress over the API ------------------------------------

def test_progress_starts_empty(client):
    body = client.get("/api/progress").json()
    assert body == {
        "quizzes_completed": 0,
        "questions_answered": 0,
        "correct_answers": 0,
        "accuracy": None,
        "activity": {mode: 0 for mode in config.MODES},
        "recent": [],
    }


def test_completed_quiz_is_scored_and_counted(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    quiz = make_quiz(client, topic="alpha")
    message_id = quiz["message"]["id"]

    res = client.post(f"/api/messages/{message_id}/quiz-attempts", json={"answers": [1, 1]})
    assert res.status_code == 201
    attempt = res.json()
    assert (attempt["correct"], attempt["total"], attempt["results"]) == (1, 2, [True, False])
    assert attempt["title"] == "Quiz me on alpha"
    assert attempt["scope"] == "alpha"
    assert attempt["conversation_id"] == quiz["conversation"]["id"]

    # A retry is a second attempt.
    client.post(f"/api/messages/{message_id}/quiz-attempts", json={"answers": [1, 0]})

    body = client.get("/api/progress").json()
    assert body["quizzes_completed"] == 2
    assert body["questions_answered"] == 4
    assert body["correct_answers"] == 3
    assert body["accuracy"] == 0.75
    assert [(a["correct"], a["total"]) for a in body["recent"]] == [(2, 2), (1, 2)]  # newest first
    assert body["activity"]["quiz"] == 1

    # The reopened chat shows the latest score with the quiz.
    saved = client.get(f"/api/conversations/{quiz['conversation']['id']}").json()
    assert saved["messages"][1]["last_attempt"]["correct"] == 2
    assert saved["messages"][0]["last_attempt"] is None


def test_progress_survives_deleting_the_chat(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    quiz = make_quiz(client)
    client.post(f"/api/messages/{quiz['message']['id']}/quiz-attempts", json={"answers": [1, 0]})
    client.delete(f"/api/conversations/{quiz['conversation']['id']}")

    body = client.get("/api/progress").json()
    assert body["quizzes_completed"] == 1 and body["correct_answers"] == 2
    assert body["recent"][0]["conversation_id"] is None
    assert body["recent"][0]["message_id"] is None
    assert body["activity"]["quiz"] == 0  # activity counts the saved chats


def test_quiz_attempt_errors(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    quiz = make_quiz(client)
    answer = chat(client, question="What is alpha?", conversation_id=quiz["conversation"]["id"]).json()

    url = f"/api/messages/{quiz['message']['id']}/quiz-attempts"
    assert client.post(url, json={"answers": [1]}).status_code == 400
    assert client.post(url, json={"answers": [1, 7]}).status_code == 400
    assert client.post(url, json={}).status_code == 422
    # Not a quiz, or no such message.
    assert client.post(f"/api/messages/{answer['message']['id']}/quiz-attempts", json={"answers": [0]}).status_code == 404
    assert client.post("/api/messages/9999/quiz-attempts", json={"answers": [0]}).status_code == 404
    assert client.get("/api/progress").json()["quizzes_completed"] == 0


def test_activity_counts_each_mode(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    chat(client, question="Q")
    chat(client, question="Q2")
    chat(client, mode="flashcards")
    activity = client.get("/api/progress").json()["activity"]
    assert (activity["ask"], activity["flashcards"], activity["summarize"]) == (2, 1, 0)


# --- Subjects and courses --------------------------------------------------------

def test_assign_edit_and_remove_a_subject(client):
    doc = upload(client, "cells.pdf", make_pdf(["Cells"])).json()
    assert (doc["subject"], doc["course"]) == ("", "")

    res = client.patch(f"/api/documents/{doc['id']}", json={"subject": "  Biology ", "course": "BIO  101"})
    assert res.status_code == 200
    assert (res.json()["subject"], res.json()["course"]) == ("Biology", "BIO 101")

    # Another document reuses the existing spelling.
    other = upload(client, "genes.pdf", make_pdf(["Genes"])).json()
    res = client.patch(f"/api/documents/{other['id']}", json={"subject": "biology", "course": "bio 101"})
    assert (res.json()["subject"], res.json()["course"]) == ("Biology", "BIO 101")

    # Removing the subject removes the course too.
    res = client.patch(f"/api/documents/{doc['id']}", json={"subject": ""})
    assert (res.json()["subject"], res.json()["course"]) == ("", "")
    listed = {d["filename"]: d["subject"] for d in client.get("/api/documents").json()}
    assert listed == {"cells.pdf": "", "genes.pdf": "Biology"}

    assert client.patch("/api/documents/missing", json={"subject": "X"}).status_code == 404


def test_upload_into_a_subject(client):
    client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["Alpha"]), "application/pdf")},
                data={"subject": "Chemistry"})
    assert client.get("/api/documents").json()[0]["subject"] == "Chemistry"


def test_subject_limits_retrieval(client, prompts):
    a = upload(client, "a.pdf", make_pdf(["Alpha text", "More alpha"])).json()
    b = upload(client, "b.pdf", make_pdf(["Beta text"])).json()
    upload(client, "c.pdf", make_pdf(["Gamma text", "More gamma"]))
    for doc in (a, b):
        client.patch(f"/api/documents/{doc['id']}", json={"subject": "Greek"})

    body = chat(client, question="Tell me", subject="greek").json()
    assert {s["source"] for s in body["message"]["sources"]} <= {"a.pdf", "b.pdf"}
    assert body["question"]["scope"] == "Greek"
    assert "Gamma" not in prompts[-1]

    # A summary of the subject covers all of its documents and nothing else.
    body = chat(client, mode="summarize", subject="Greek", topic="letters").json()
    assert {(s["source"], s["page"]) for s in body["message"]["sources"]} == {("a.pdf", 1), ("a.pdf", 2), ("b.pdf", 1)}
    assert body["question"]["scope"] == "Greek · letters"
    assert body["question"]["content"] == "Summarize letters"

    body = chat(client, mode="quiz", subject="Greek").json()
    assert body["question"]["content"] == "Quiz me on Greek"
    assert json.loads(body["message"]["content"])["request"]["subject"] == "Greek"

    # A selected document wins over the subject.
    sources = chat(client, question="Tell me", doc_id=b["id"], subject="Greek").json()["message"]["sources"]
    assert {s["source"] for s in sources} == {"b.pdf"}

    res = chat(client, question="Tell me", subject="Latin")
    assert res.status_code == 404 and "No documents are filed under Latin" in res.json()["detail"]


def test_scope_filter_for_a_subject():
    assert rag.scope_filter("u", doc_ids=["d1", "d2"]) == {"$and": [{"user_id": "u"}, {"doc_id": {"$in": ["d1", "d2"]}}]}
    assert rag.scope_filter("u", "d1", doc_ids=["d2"]) == {"$and": [{"user_id": "u"}, {"doc_id": "d1"}]}


# --- Retrieval quality and source excerpts ----------------------------------------

def topic_embed(texts):
    """Embeds by keyword, so relevance is predictable."""
    axes = ("alpha", "beta", "gamma")
    vectors = []
    for text in texts:
        vec = [1.0 if word in text.lower() else 0.0 for word in axes] + [0.1]
        norm = sum(v * v for v in vec) ** 0.5
        vectors.append([v / norm for v in vec])
    return vectors


def test_weak_matches_are_left_out(client, monkeypatch, prompts):
    monkeypatch.setattr(rag, "embed", topic_embed)
    monkeypatch.setattr(config, "RELEVANCE_MARGIN", 0.15)
    upload(client, "a.pdf", make_pdf(["Alpha is the first letter.", "Beta is the second.", "Gamma is third."]))

    body = chat(client, question="What is alpha?").json()
    assert [s["page"] for s in body["message"]["sources"]] == [1]
    assert "Beta" not in prompts[-1] and "Gamma" not in prompts[-1]

    # The closest passage is always sent, even when nothing matches well.
    body = chat(client, question="What is photosynthesis?").json()
    assert len(body["message"]["sources"]) >= 1

    # Study modes use the whole scope, not just the closest passages.
    body = chat(client, mode="summarize", topic="alpha").json()
    assert [s["page"] for s in body["message"]["sources"]] == [1, 2, 3]


def test_sources_have_a_short_relevant_excerpt_and_the_full_passage(client):
    filler = " ".join(f"Filler sentence {i} about nothing." for i in range(12))
    text = f"{filler} Mitochondria produce ATP for the cell. {filler}"
    doc = pymupdf_doc_with_paragraph(text)
    upload(client, "bio.pdf", doc)

    sources = chat(client, question="What do mitochondria produce?").json()["message"]["sources"]
    with_atp = [s for s in sources if "Mitochondria produce ATP" in s["text"]]
    assert with_atp
    source = with_atp[0]
    assert "Mitochondria produce ATP for the cell." in source["excerpt"]
    assert len(source["excerpt"]) <= config.EXCERPT_CHARS + 4
    assert len(source["text"]) > len(source["excerpt"])
    assert (source["source"], source["page"]) == ("bio.pdf", 1)


def pymupdf_doc_with_paragraph(text):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def test_outdated_documents_are_reindexed(client):
    from app.main import recover_index

    doc = upload(client, "a.pdf", make_pdf(["Alpha text", "More alpha"])).json()
    missing = upload(client, "b.pdf", make_pdf(["Beta text"])).json()
    with database.begin() as conn:
        conn.execute(database.documents.update().values(index_version=1, chunks=99))
    stored_path(USER_A, missing["id"]).unlink()  # can't be re-read

    recover_index()

    docs = {d["id"]: d for d in database.list_documents(USER_A)}
    assert (docs[doc["id"]]["index_version"], docs[doc["id"]]["chunks"]) == (config.INDEX_VERSION, 2)
    assert rag.document_chunk_count(doc["id"]) == 2
    # The one whose file is gone is marked failed, and keeps its old chunks.
    assert docs[missing["id"]]["status"] == "failed"
    assert "missing" in docs[missing["id"]]["error"]
    assert rag.document_chunk_count(missing["id"]) == 1
