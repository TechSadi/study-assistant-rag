"""Study modes, document/page/topic scope, and structured quiz and flashcard output."""

import json

from app import config, rag

from .conftest import USER_A, make_pdf, upload


def chat(client, **body):
    return client.post("/api/chat", json=body)


# --- Modes over the API -----------------------------------------------------

def test_explain_mode_uses_the_tutor_style(client, prompts):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    res = chat(client, question="What is alpha?", mode="explain")
    assert res.status_code == 200
    body = res.json()
    assert body["message"]["mode"] == "explain"
    assert body["question"]["mode"] == "explain"
    assert "patient tutor" in prompts[-1]
    assert "Answer the question using only the provided context." in prompts[-1]


def test_ask_mode_is_unchanged(client, prompts):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    body = chat(client, question="What is alpha?").json()
    assert body["message"]["mode"] == "ask"
    assert "patient tutor" not in prompts[-1]


def test_summarize_without_text_names_the_document(client, prompts):
    doc = upload(client, "biology.pdf", make_pdf(["Cells are small.", "Mitochondria make ATP."])).json()
    res = chat(client, mode="summarize", doc_id=doc["id"])
    assert res.status_code == 200
    body = res.json()
    assert body["question"]["content"] == "Summarize biology.pdf"
    assert body["question"]["scope"] == "biology.pdf"
    assert body["conversation"]["title"] == "Summarize biology.pdf"
    # A small document is summarized whole, in page order.
    assert [s["page"] for s in body["message"]["sources"]] == [1, 2]
    assert "study summary" in prompts[-1]


def test_ask_and_explain_still_need_a_question(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    assert chat(client, question="  ").status_code == 400
    assert chat(client, mode="explain").status_code == 400


def test_unknown_mode_is_rejected(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    assert chat(client, question="Q", mode="essay").status_code == 422


def test_quiz_is_saved_as_clean_json(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    body = chat(client, mode="quiz", topic="alpha").json()
    assert body["question"]["content"] == "Quiz me on alpha"

    quiz = json.loads(body["message"]["content"])
    # Short-answer, broken and duplicate-option questions are dropped.
    assert [q["question"] for q in quiz["questions"]] == ["What is alpha?", "Which letter comes first?"]
    first, second = quiz["questions"]
    assert first["type"] == "multiple_choice" and first["answer"] == "A fish"  # "B" -> option text
    assert (first["source"], first["page"]) == ("a.pdf", 1)
    assert second["source"] is None and second["page"] is None  # cited a page that wasn't sent
    # What the quiz covered is kept for "New quiz".
    assert quiz["request"] == {"doc_id": None, "subject": None, "topic": "alpha", "page_from": None, "page_to": None}

    saved = client.get(f"/api/conversations/{body['conversation']['id']}").json()
    assert saved["messages"][1]["mode"] == "quiz"


def test_flashcards(client):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    body = chat(client, mode="flashcards").json()
    assert body["question"]["content"] == "Make flashcards for my documents"
    cards = json.loads(body["message"]["content"])["cards"]
    # The blank card is dropped; the missing file name is filled in from the page.
    assert cards == [{"front": "Alpha?", "back": "The first letter.", "source": "a.pdf", "page": 1}]


def test_unusable_quiz_reply_is_an_error_and_saves_nothing(client, monkeypatch):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    monkeypatch.setattr(rag, "call_gemini", lambda prompt, json_output=False: "not json")
    res = chat(client, mode="quiz")
    assert res.status_code == 503
    assert "couldn't be read" in res.json()["detail"]

    monkeypatch.setattr(rag, "call_gemini", lambda prompt, json_output=False: '{"questions": []}')
    assert chat(client, mode="quiz").status_code == 503
    assert client.get("/api/conversations").json() == []


def test_follow_up_after_a_quiz_sees_the_questions(client, prompts):
    upload(client, "a.pdf", make_pdf(["Alpha text"]))
    cid = chat(client, mode="quiz").json()["conversation"]["id"]
    res = chat(client, question="Why is question 1 right?", mode="explain", conversation_id=cid)
    assert res.status_code == 200
    assert "1. What is alpha? Options: A letter; A fish; A car; A tree. Answer: A fish" in prompts[-1]


# --- Document, page and topic scope -----------------------------------------

def test_selected_document_limits_retrieval(client):
    a = upload(client, "a.pdf", make_pdf(["Alpha text", "More alpha"])).json()
    upload(client, "b.pdf", make_pdf(["Beta text", "More beta"]))

    sources = chat(client, question="Tell me", doc_id=a["id"]).json()["message"]["sources"]
    assert sources and {s["source"] for s in sources} == {"a.pdf"}


def test_page_range_limits_retrieval(client):
    doc = upload(client, "a.pdf", make_pdf([f"Page {i} text" for i in range(1, 6)])).json()
    body = chat(client, mode="summarize", doc_id=doc["id"], page_from=2, page_to=3).json()
    assert [s["page"] for s in body["message"]["sources"]] == [2, 3]
    assert body["question"]["scope"] == "a.pdf · pages 2–3"


def test_page_range_errors(client):
    doc = upload(client, "a.pdf", make_pdf(["One", "", "Three"])).json()
    assert chat(client, question="Q", page_from=1).status_code == 400  # no document
    assert chat(client, question="Q", doc_id=doc["id"], page_from=3, page_to=2).status_code == 400
    res = chat(client, question="Q", doc_id=doc["id"], page_from=9)
    assert res.status_code == 400 and "only has 3 pages" in res.json()["detail"]
    res = chat(client, question="Q", doc_id=doc["id"], page_from=2, page_to=2)  # a blank page
    assert res.status_code == 422
    assert "No text was found in a.pdf page 2" in res.json()["detail"]
    assert chat(client, question="Q", doc_id="missing").status_code == 404


def test_topic_is_searched_and_shown(client, prompts):
    doc = upload(client, "a.pdf", make_pdf(["Alpha text"])).json()
    body = chat(client, question="Explain it", doc_id=doc["id"], topic="enzymes").json()
    assert body["question"]["scope"] == "a.pdf · enzymes"
    assert "The student is focusing on: enzymes" in prompts[-1]


# --- Pipeline pieces --------------------------------------------------------

def test_scope_filter():
    assert rag.scope_filter("u") == {"user_id": "u"}
    assert rag.scope_filter("u", "d1") == {"$and": [{"user_id": "u"}, {"doc_id": "d1"}]}
    assert rag.scope_filter("u", "d1", (2, 4)) == {
        "$and": [{"user_id": "u"}, {"doc_id": "d1"}, {"page": {"$gte": 2}}, {"page": {"$lte": 4}}]
    }


def test_study_context_samples_a_large_scope_evenly(client, monkeypatch):
    doc = upload(client, "long.pdf", make_pdf([f"Page {i}" for i in range(1, 21)])).json()
    where = rag.scope_filter(USER_A, doc["id"])

    whole = rag.study_context("", where, limit=25)
    assert [c["page"] for c in whole] == list(range(1, 21))

    sampled = rag.study_context("", where, limit=5)
    assert [c["page"] for c in sampled] == [1, 5, 9, 13, 17]

    searched = rag.study_context("Page 7", where, limit=5)
    pages = [c["page"] for c in searched]
    assert len(pages) == 5 and pages == sorted(pages)  # reading order


def test_retrieval_query_adds_the_topic():
    assert rag.retrieval_query("What is it?", [], "enzymes") == "enzymes\nWhat is it?"


def test_parse_json_accepts_a_code_fence():
    assert rag.parse_json('```json\n{"cards": []}\n```') == {"cards": []}


def test_option_index():
    options = ["Red", "Green", "Blue"]
    assert rag._option_index("green", options) == 1
    assert rag._option_index("C)", options) == 2
    assert rag._option_index("(a)", options) == 0
    assert rag._option_index("D", options) is None
    assert rag._option_index("Purple", options) is None


def test_follow_up_about_a_quiz_question_searches_for_it():
    quiz = {"questions": [
        {"question": "What is AES?", "answer": "A block cipher"},
        {"question": "How fast is RSA?", "answer": "100 to 1000 times slower than symmetric ciphers"},
    ]}
    history = [
        {"role": "user", "content": "Quiz me on encryption", "mode": "quiz"},
        {"role": "assistant", "content": json.dumps(quiz), "mode": "quiz"},
    ]
    query = rag.retrieval_query("Why is question 2 correct?", history)
    assert "How fast is RSA? 100 to 1000 times slower" in query
    assert "What is AES?" not in query
    assert rag.referenced_item("Why is question 9 correct?", history) is None
    assert rag.referenced_item("Why is that correct?", history) is None
    assert rag.referenced_item("Explain question 1", history[:1]) is None  # no quiz yet


def test_follow_up_about_a_quiz_question_includes_its_page(client, prompts):
    upload(client, "a.pdf", make_pdf([f"Page {i} text" for i in range(1, 8)]))
    cid = chat(client, mode="quiz").json()["conversation"]["id"]  # stub question 1 cites a.pdf page 1
    body = chat(client, question="Why is question 1 right?", conversation_id=cid).json()
    assert ("a.pdf", 1) in {(s["source"], s["page"]) for s in body["message"]["sources"]}
    assert "Source: a.pdf, Page: 1\nPage 1 text" in prompts[-1]
