import pymupdf

from app import database, rag


def test_split_text_keeps_chunks_within_a_page():
    pages = [
        {"text": "a" * 2000, "source": "notes.pdf", "page": 1},
        {"text": "short page", "source": "notes.pdf", "page": 2},
    ]
    chunks = rag.split_text(pages, chunk_size=800, overlap=150)

    page_one = [c for c in chunks if c["page"] == 1]
    # One unbroken "sentence" is cut at 0-800, 650-1450 and 1300-2000.
    assert len(page_one) == 3
    assert all(len(c["text"]) <= 800 for c in chunks)
    assert chunks[-1] == {"text": "short page", "source": "notes.pdf", "page": 2}


def test_split_text_overlaps_a_cut_sentence():
    text = "".join(str(i % 10) for i in range(1000))
    chunks = rag.split_text([{"text": text, "source": "x.pdf", "page": 1}], chunk_size=800, overlap=150)
    assert chunks[0]["text"][-150:] == chunks[1]["text"][:150]


def sentences(n, word):
    return " ".join(f"{word} sentence number {i} is here." for i in range(n))


def test_split_text_ends_chunks_on_sentences_and_overlaps_whole_sentences():
    text = sentences(20, "Alpha")  # about 600 characters, one paragraph
    chunks = rag.split_text([{"text": text, "source": "x.pdf", "page": 1}], chunk_size=200, overlap=70)
    assert len(chunks) > 3 and all(len(c["text"]) <= 200 for c in chunks)
    for c in chunks:
        assert c["text"].startswith("Alpha") and c["text"].endswith("here.")
    # The next chunk starts by repeating the last whole sentences (up to 70 chars) of the one before.
    repeated = chunks[1]["text"][:chunks[1]["text"].index(".") + 1]
    assert chunks[0]["text"].endswith(chunks[1]["text"][:70].rsplit(". ", 1)[0] + ".")
    assert repeated in chunks[0]["text"]


def test_split_text_keeps_paragraphs_together():
    first, second = sentences(3, "One"), sentences(3, "Two")  # about 90 characters each
    third = sentences(3, "Three")
    text = f"{first}\n\n{second}\n\nHeading\n\n{third}"
    chunks = rag.split_text([{"text": text, "source": "x.pdf", "page": 1}], chunk_size=200, overlap=0)
    assert [c["text"] for c in chunks] == [
        f"{first}\n\n{second}",
        # The heading moves on with the paragraph it introduces.
        f"Heading\n\n{third}",
    ]


def test_page_text_joins_wrapped_lines_into_paragraphs():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A heading")
    paragraph = "This paragraph is long enough that it wraps onto more than one line in the box."
    page.insert_textbox(pymupdf.Rect(72, 100, 300, 300), paragraph)
    assert rag.page_text(page) == f"A heading\n\n{paragraph}"
    doc.close()


def test_best_excerpt_picks_the_matching_sentences():
    text = sentences(6, "Filler") + " RSA uses a public key and a private key. " + sentences(6, "More")
    excerpt = rag.best_excerpt(text, "How does RSA use keys?", max_chars=120)
    assert "RSA uses a public key and a private key." in excerpt
    assert len(excerpt) <= 124 and excerpt.startswith("… ") and excerpt.endswith(" …")
    assert rag.best_excerpt("Short passage.", "anything") == "Short passage."
    # Paragraph breaks are kept, and a heading isn't left dangling at the end.
    passage = f"Integrity\n\nData must not change unnoticed. {sentences(3, 'Hash')}\n\nAvailability\n\n{sentences(4, 'Uptime')}"
    assert rag.best_excerpt(passage, "what is integrity", max_chars=150).startswith("Integrity\n\nData must not")
    assert "Availability" not in rag.best_excerpt(passage, "what is integrity", max_chars=150)
    # Short table rows are kept together.
    table = "\n\n".join(["Aspect Symmetric Asymmetric", "Keys One shared key Key pair", "Speed Fast Slow"] * 2)
    assert rag.best_excerpt(table + "\n\n" + sentences(8, "Tail"), "symmetric keys", max_chars=150).startswith(
        "Aspect Symmetric Asymmetric\n\nKeys One shared key Key pair\n\nSpeed Fast Slow"
    )
    # Nothing matches: the passage's start.
    assert rag.best_excerpt(text, "photosynthesis", max_chars=60).startswith("Filler sentence number 0")


def test_build_prompt_keeps_grounding_instruction():
    prompt = rag.build_prompt("What is RSA?", [{"text": "RSA is...", "source": "c.pdf", "page": 4}])
    assert "using only the provided context" in prompt
    assert "If the context does not contain the answer, say so." in prompt
    assert "Source: c.pdf, Page: 4" in prompt
    assert "CONVERSATION HISTORY" not in prompt


def test_build_prompt_with_history_marks_it_as_not_a_source():
    history = [
        {"role": "user", "content": "What is RSA?"},
        {"role": "assistant", "content": "RSA is a public-key cipher."},
    ]
    prompt = rag.build_prompt("Who invented it?", [{"text": "...", "source": "c.pdf", "page": 4}], history)
    assert "Student: What is RSA?" in prompt
    assert "Assistant: RSA is a public-key cipher." in prompt
    assert "It is not a source of facts" in prompt
    assert "Answer the question using only the provided context." in prompt


def test_history_is_limited_and_trimmed():
    history = [{"role": "user", "content": f"q{i} " + "x" * 5000} for i in range(10)]
    text = rag.format_history(history)
    assert "q3 " not in text and "q4 " in text  # only the last 6 messages
    assert "[…]" in text


def test_retrieval_query_includes_previous_question():
    assert rag.retrieval_query("What is RSA?", []) == "What is RSA?"
    history = [{"role": "user", "content": "What is RSA?"}, {"role": "assistant", "content": "..."}]
    assert rag.retrieval_query("Who invented it?", history) == "What is RSA?\nWho invented it?"


def test_make_title():
    assert database.make_title("  What   is RSA? ") == "What is RSA?"
    long = "Explain the difference between symmetric and asymmetric encryption in simple words please"
    title = database.make_title(long)
    assert len(title) <= 61 and title.endswith("…")
    assert long.startswith(title[:-1])


def test_strip_preamble():
    assert rag.strip_preamble("Based on the provided context, symmetric is faster.") == "Symmetric is faster."
    assert rag.strip_preamble("According to the documents: RSA uses two keys.") == "RSA uses two keys."
    assert rag.strip_preamble("Based on the context provided, **AES** wins.") == "**AES** wins."
    unchanged = "Symmetric encryption uses one shared key."
    assert rag.strip_preamble(unchanged) == unchanged


def test_prompt_asks_for_a_direct_answer():
    prompt = rag.build_prompt("Q?", [{"text": "...", "source": "c.pdf", "page": 1}])
    assert 'Don\'t open with phrases like "Based on the provided context"' in prompt


def test_falls_back_when_main_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(rag.config, "GEMINI_MODEL", "main")
    monkeypatch.setattr(rag.config, "GEMINI_FALLBACK_MODELS", ["backup"])
    tried = []

    def fake_generate(model, prompt, json_output=False):
        tried.append(model)
        if model == "main":
            raise rag._ModelUnavailable("busy")
        return "Based on the provided context, it works."

    monkeypatch.setattr(rag, "_generate", fake_generate)
    assert rag.call_gemini("prompt") == "It works."
    assert tried == ["main", "backup"]


def test_reports_when_every_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(rag.config, "GEMINI_FALLBACK_MODELS", ["backup"])

    def fake_generate(model, prompt, json_output=False):
        raise rag._ModelUnavailable("daily_quota")

    monkeypatch.setattr(rag, "_generate", fake_generate)
    try:
        rag.call_gemini("prompt")
        raise AssertionError("expected GenerationError")
    except rag.GenerationError as e:
        assert "daily AI quota" in str(e)


def test_a_temporary_failure_is_reported_over_a_daily_quota(monkeypatch):
    monkeypatch.setattr(rag.config, "GEMINI_MODEL", "main")
    monkeypatch.setattr(rag.config, "GEMINI_FALLBACK_MODELS", ["backup"])

    def fake_generate(model, prompt, json_output=False):
        raise rag._ModelUnavailable("daily_quota" if model == "main" else "rate_limit")

    monkeypatch.setattr(rag, "_generate", fake_generate)
    try:
        rag.call_gemini("prompt")
        raise AssertionError("expected GenerationError")
    except rag.GenerationError as e:
        assert "rate limit" in str(e) and "tomorrow" not in str(e)
