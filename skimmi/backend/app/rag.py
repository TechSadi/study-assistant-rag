"""Skimmi AI's RAG pipeline: the only place documents are processed and questions answered.

  extract_pages -> split_text -> index_chunks -> retrieve_context -> generate_answer

Study modes reuse the same pieces: `run_mode` dispatches to generate_answer (ask),
explain, summarize, make_quiz or make_flashcards. Retrieval is always limited to
one user's chunks, and optionally to one document, page range or subject, with
`scope_filter`.

* The embedding model and Gemini client are created once, on first use, and
  reused for every request. Chunks are stored through vectors.py (ChromaDB).
* Each document's chunks carry its UUID (`doc_id`) and its owner (`user_id`), so a
  document can be added or removed on its own without rebuilding the collection,
  and one user's search can never reach another user's chunks.
"""

import json
import logging
import re
import time
from functools import lru_cache

from . import config, vectors
from .extract import extract_pages, page_text  # noqa: F401  (page_text: used by tests)

log = logging.getLogger("skimmi.rag")


class GenerationError(Exception):
    """Gemini could not produce an answer."""


# --- Shared clients (created once) -----------------------------------------

@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model %s", config.EMBEDDING_MODEL)
    return SentenceTransformer(config.EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def get_llm():
    from google import genai

    api_key = config.get_gemini_api_key()
    if not api_key:
        log.error("GEMINI_API_KEY is not set. Add it to skimmi/backend/.env and restart the backend.")
        raise GenerationError("The AI service isn't set up correctly. Try again later.")
    # A request that hangs is cut off rather than holding the worker (milliseconds).
    return genai.Client(
        api_key=api_key, http_options=genai.types.HttpOptions(timeout=int(config.GEMINI_TIMEOUT * 1000))
    )


def embed(texts):
    """Embed a list of strings. Indexing and retrieval must both normalize."""
    return get_embedding_model().encode(texts, normalize_embeddings=True).tolist()


# --- 1. Load a document ----------------------------------------------------
# extract.py reads each file type into pages of text (PDF, Word, PowerPoint, ...).

# --- 2. Split into chunks --------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _hard_split(text, size, overlap):
    """Pieces of a "sentence" longer than a chunk (a table, a formula, a list of
    URLs), cut at spaces where possible, each overlapping the one before."""
    pieces, start = [], 0
    while True:
        end = start + size
        if end >= len(text):
            pieces.append(text[start:].strip())
            return [p for p in pieces if p]
        space = text.rfind(" ", start + size // 2, end)
        end = space if space > 0 else end
        pieces.append(text[start:end].strip())
        start = end - overlap
        space = text.find(" ", start, end)
        if 0 <= space < end - 1:  # start the overlap on a word
            start = space + 1


def _units(text, chunk_size, overlap):
    """A page as sentence units: (text, starts_paragraph, paragraph_length).
    A sentence longer than a whole chunk is cut into overlapping pieces."""
    units = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        first = True
        for sentence in _SENTENCE_END.split(paragraph) if paragraph else []:
            pieces = [sentence] if len(sentence) <= chunk_size else _hard_split(sentence, chunk_size, overlap)
            for piece in pieces:
                units.append((piece, first, len(paragraph)))
                first = False
    return units


def _is_heading(unit):
    """A short paragraph of its own that doesn't end like a sentence ("1.3 Integrity")."""
    piece, starts_paragraph, paragraph_length = unit
    return starts_paragraph and paragraph_length == len(piece) <= 80 and not piece.endswith((".", "!", "?"))


def _join(units):
    text = ""
    for piece, starts_paragraph, _ in units:
        text += (("\n\n" if starts_paragraph else " ") + piece) if text else piece
    return text


def split_text(pages, chunk_size=config.CHUNK_SIZE, overlap=config.CHUNK_OVERLAP):
    """Chunks of up to chunk_size characters, built from whole sentences.

    Chunks never cross a page, so every chunk has one page number. A paragraph
    that fits in a chunk is kept together: if it doesn't fit in the current chunk,
    it starts the next one. Each chunk begins with the last sentences (up to
    `overlap` characters) of the one before, so an idea that straddles a boundary
    is still found. Only a sentence longer than a whole chunk is cut.
    """
    chunks = []
    for page in pages:
        current = []
        for unit in _units(page["text"], chunk_size, overlap):
            piece, starts_paragraph, paragraph_length = unit
            # Room needed now: the whole paragraph if it can fit in one chunk.
            need = paragraph_length if starts_paragraph and paragraph_length <= chunk_size else len(piece)
            if current and len(_join(current)) + 2 + need > chunk_size:
                # A heading at the end of the chunk moves on with the paragraph it introduces.
                headings = []
                while len(current) > 1 and _is_heading(current[-1]):
                    headings.insert(0, current.pop())
                if headings and len(_join(headings)) + 2 + need > chunk_size:
                    current, headings = current + headings, []
                chunks.append({"text": _join(current), "source": page["source"], "page": page["page"]})
                carry = []
                for previous in reversed(current):
                    if len(_join([previous] + carry)) > overlap:
                        break
                    carry.insert(0, previous)
                if not carry or len(_join(carry + headings)) + 2 + need > chunk_size:
                    carry = []
                current = carry + headings
            current.append(unit)
        if current:
            chunks.append({"text": _join(current), "source": page["source"], "page": page["page"]})
    return chunks


# --- 3. Embed and store ----------------------------------------------------

def index_chunks(doc_id, chunks, user_id, embeddings=None):
    """Add one document's chunks to the vector store, tagged with its owner."""
    if not user_id:
        raise ValueError("Chunks need an owner")
    texts = [c["text"] for c in chunks]
    vectors.add(
        ids=[f"{doc_id}_{i}" for i in range(len(chunks))],
        texts=texts,
        embeddings=embeddings or embed(texts),
        metadatas=[
            {"user_id": user_id, "doc_id": doc_id, "source": c["source"], "page": c["page"]} for c in chunks
        ],
    )


def ingest_document(doc_id, path, display_name, user_id):
    """Extract, chunk and index an uploaded file. Returns (page_count, chunk_count).

    Raises ValueError if the file can't be read or has no text. If indexing fails
    part-way, any chunks already stored for this document are removed.
    """
    try:
        page_count, pages = extract_pages(path, display_name)
    except ValueError:
        raise
    except Exception:
        raise ValueError("The file may be damaged.")

    if not pages:
        hint = " Scanned or image-only PDFs aren't supported yet." if str(path).lower().endswith(".pdf") else ""
        raise ValueError("No text was found." + hint)

    chunks = split_text(pages)
    try:
        index_chunks(doc_id, chunks, user_id)
    except Exception:
        remove_document(doc_id)
        raise
    return page_count, len(chunks)


def reindex_document(doc_id, path, display_name, user_id):
    """Re-chunk and re-embed a document from its stored file (after chunking changes,
    or when its chunks were lost). Returns (page_count, chunk_count).

    The new chunks are built and embedded before the old ones are removed, so if
    anything fails the document keeps its old chunks and still works.
    """
    page_count, pages = extract_pages(path, display_name)
    chunks = split_text(pages)
    if not chunks:
        raise ValueError("No text was found.")
    embeddings = embed([c["text"] for c in chunks])
    remove_document(doc_id)
    index_chunks(doc_id, chunks, user_id, embeddings)
    return page_count, len(chunks)


def remove_document(doc_id):
    vectors.delete_document(doc_id)


def document_chunk_count(doc_id):
    return vectors.count({"doc_id": doc_id})


def chunk_count():
    return vectors.count()


# --- 4. Retrieve -----------------------------------------------------------

def scope_filter(user_id, doc_id=None, pages=None, doc_ids=None):
    """A ChromaDB `where` clause for one user's chunks: all of them, one document
    and, optionally, an inclusive page range (first, last), or a group of documents
    (a subject's). The document ids must already be checked to be the user's; the
    user_id condition is there as well, so a mistake can't leak another user's chunks."""
    if not user_id:
        raise ValueError("Retrieval needs a user")
    conditions = [{"user_id": user_id}]
    if doc_id:
        conditions.append({"doc_id": doc_id})
    elif doc_ids:
        conditions.append({"doc_id": {"$in": list(doc_ids)}})
    if pages:
        first, last = pages
        conditions += [{"page": {"$gte": first}}, {"page": {"$lte": last}}]
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _check_scope(where):
    """Searches must come from scope_filter, so they are limited to one user."""
    conditions = where.get("$and", [where]) if isinstance(where, dict) else []
    if not any(isinstance(c, dict) and "user_id" in c for c in conditions):
        raise ValueError("Retrieval must be limited to one user (use scope_filter)")
    return where


def scope_size(where):
    """How many chunks a scope contains."""
    return vectors.count(_check_scope(where))


def _context(text, meta):
    return {"text": text, "doc_id": meta.get("doc_id"), "source": meta["source"], "page": meta["page"]}


def _reading_key(context):
    return context["source"].lower(), context["page"]


def retrieve_context(query, top_k=config.TOP_K, where=None, trim=True):
    """The chunks most similar to the query, best first.

    With `trim`, chunks much further from the query than the best match
    (RELEVANCE_MARGIN) are left out, so Gemini isn't handed passages that only
    happen to rank in the top k. The best match is always kept, so a question the
    documents don't cover is still checked against the closest passage.
    Chunks with the same text (a repeated header, say) are sent once.
    """
    total = scope_size(where)
    if total == 0:
        return []

    rows = vectors.query(embed([query])[0], min(top_k, total), where)
    cutoff = rows[0][2] + config.RELEVANCE_MARGIN if rows else 0
    contexts, seen = [], set()
    for text, meta, distance in rows:
        if (trim and distance > cutoff) or text in seen:
            continue
        seen.add(text)
        contexts.append(_context(text, meta))
    return contexts


def scope_chunks(where):
    """Every chunk in the scope, in reading order (document, page, position on the page)."""
    rows = sorted(
        vectors.get(_check_scope(where)),
        key=lambda r: (r[2]["source"].lower(), r[2]["page"], int(r[0].rsplit("_", 1)[1])),
    )
    return [_context(text, meta) for _, text, meta in rows]


def study_context(query, where, limit=config.STUDY_CHUNKS):
    """Material for a summary, quiz or flashcards, in reading order.

    A small scope (a short document or page range) is used whole. A larger one is
    narrowed by semantic search when there is something to search for (a topic or
    the student's request); otherwise it is sampled evenly so the material covers
    the whole scope rather than just its first pages.
    """
    size = scope_size(where)
    if size == 0:
        return []
    if size <= limit:
        return scope_chunks(where)
    if query:
        return sorted(retrieve_context(query, limit, where, trim=False), key=_reading_key)
    chunks = scope_chunks(where)
    step = len(chunks) / limit
    return [chunks[int(i * step)] for i in range(limit)]


def retrieval_query(question, history, topic=None):
    """Follow-ups like "explain the second one" say little on their own, so search
    with the previous question as well. The first question is searched as-is.
    A topic the student is focusing on is searched too, and so is a quiz question
    or flashcard the question points at ("why is question 2 right?")."""
    previous = [m["content"] for m in history if m["role"] == "user"]
    query = f"{previous[-1]}\n{question}" if previous else question
    item = referenced_item(question, history)
    if item:
        query = f"{query}\n{item_text(item)}"
    return f"{topic}\n{query}" if topic else query


_ITEM_REFERENCE = re.compile(r"\b(?:question|q|card|flashcard)\s*(?:no\.?|number|#)?\s*(\d{1,2})\b", re.IGNORECASE)


def referenced_item(question, history):
    """"Question N" or "card N" from the latest quiz or flashcard set, if the
    question refers to one. Returns None otherwise."""
    match = _ITEM_REFERENCE.search(question)
    structured = [m for m in history if m["role"] == "assistant" and m.get("mode") in ("quiz", "flashcards")]
    if not match or not structured:
        return None
    try:
        data = json.loads(structured[-1]["content"])
    except ValueError:
        return None
    items = data.get("questions") or data.get("cards") or []
    index = int(match.group(1)) - 1
    if not 0 <= index < len(items):
        return None
    return items[index]


def item_text(item):
    return " ".join(str(item[k]) for k in ("question", "front", "answer", "back") if item.get(k))


def with_cited_page(contexts, question, history, where=None):
    """For a question about a quiz question or flashcard, add the chunks of the page
    it cites, so the answer can be checked against its source even when search
    misses it (tables, for example, embed poorly)."""
    item = referenced_item(question, history)
    if not item or not item.get("source") or not item.get("page"):
        return contexts
    cited = {"$and": [where, {"source": item["source"]}, {"page": item["page"]}]}
    have = {c["text"] for c in contexts}
    return contexts + [c for c in scope_chunks(cited) if c["text"] not in have]


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = set(
    "the and for are but not you all any can had her was one our out has him his how its may new now "
    "see two who did get let say she too use what when where which why with this that from they them "
    "then than there their these those have does into about would could should also more most some such "
    "only other each explain describe tell give list define question questions answer page pages".split()
)


def _terms(text):
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def best_excerpt(text, query, max_chars=config.EXCERPT_CHARS):
    """The part of a chunk that best matches the query, to show under an answer.

    Picks the sentence sharing the most words with the query (the first sentence
    if none do) and adds the sentences around it while they fit. "…" marks text
    left out at either end. Paragraph breaks (after a heading, say) are kept.
    """
    units = _units(text, max_chars, 0)
    sentences = [piece for piece, _, _ in units]
    if not sentences or len(_join(units)) <= max_chars:
        return _join(units)
    terms = _terms(query)
    scores = [len(_terms(s) & terms) for s in sentences]
    best = max(range(len(sentences)), key=lambda i: (scores[i], -i))

    first = last = best
    while True:
        if last + 1 < len(units) and len(_join(units[first:last + 2])) <= max_chars:
            last += 1
        elif first > 0 and len(_join(units[first - 1:last + 1])) <= max_chars:
            first -= 1
        else:
            break
    # Drop a heading cut off from the paragraph after it (but not the rows of a
    # table, which also look like short headings).
    if last > best and last + 1 < len(units) and _is_heading(units[last]) and not _is_heading(units[last + 1]):
        last -= 1
    # The first sentence shown starts the excerpt, even if it began a paragraph.
    excerpt = _join([(units[first][0], False, 0)] + units[first + 1:last + 1])
    before = "… " if first > 0 else ""
    after = " …" if last < len(units) - 1 else ""
    return f"{before}{excerpt}{after}"


# --- 5. Generate -----------------------------------------------------------

def as_text(message):
    """Quizzes and flashcards are stored as JSON. The prompt gets them as plain text."""
    mode = message.get("mode")
    if message["role"] != "assistant" or mode not in ("quiz", "flashcards"):
        return message["content"]
    try:
        data = json.loads(message["content"])
    except ValueError:
        return message["content"]

    if mode == "quiz":
        lines = ["Quiz:"]
        for i, q in enumerate(data.get("questions", []), 1):
            options = f" Options: {'; '.join(q['options'])}." if q.get("options") else ""
            lines.append(f"{i}. {q['question']}{options} Answer: {q['answer']}")
    else:
        lines = ["Flashcards:"]
        for i, card in enumerate(data.get("cards", []), 1):
            lines.append(f"{i}. {card['front']} -> {card['back']}")
    return "\n".join(lines)


def format_history(history):
    lines = []
    for m in history[-config.HISTORY_MESSAGES:]:
        text = as_text(m)
        # A quiz or flashcard set is kept longer, so "explain question 7" still works.
        limit = config.HISTORY_MESSAGE_CHARS * (3 if text is not m["content"] else 1)
        if len(text) > limit:
            text = text[:limit] + " […]"
        speaker = "Student" if m["role"] == "user" else "Assistant"
        lines.append(f"{speaker}: {text}")
    return "\n\n".join(lines)


def _context_block(contexts):
    return "\n\n".join(f"Source: {c['source']}, Page: {c['page']}\n{c['text']}" for c in contexts)


def _history_block(history):
    if not history:
        return ""
    return f"""
    Use the conversation history only to understand what the question refers to
    (for example "it", "that" or "the second one"). It is not a source of facts:
    every fact in your answer must come from the CONTEXT.

    CONVERSATION HISTORY:
    {format_history(history)}
    """


def _focus_line(topic):
    return f"\n    The student is focusing on: {topic}\n" if topic else ""


EXPLAIN_STYLE = """
    Explain like a patient tutor talking to a beginner: plain language, short
    paragraphs, and define each technical term the first time you use it.
    Build up step by step from the basic idea to the details.
    Where it helps understanding, add a short example or analogy. If the example
    is your own rather than from the documents, say so (for example "For instance,
    imagine…"), and never use it to introduce new facts.
    """


def build_prompt(question, contexts, history=(), style="", topic=None):
    """The question-answering prompt, used by the "ask" and "explain" modes."""
    return f"""
    You are a helpful study assistant.
    Answer the question using only the provided context.
    If the context does not contain the answer, say so.
    Do not invent facts.
    Answer directly. Don't open with phrases like "Based on the provided context"
    and don't refer to "the context"; if something isn't covered, say it isn't
    in the uploaded documents.
    {style}{_focus_line(topic)}{_history_block(history)}
    CONTEXT:
    {_context_block(contexts)}

    QUESTION:
    {question}

    ANSWER:
    """


SUMMARY_TASK = """
    TASK: Write a concise study summary of the material in the CONTEXT that the
    student asked for.
    - Open with a one or two sentence overview.
    - Then give each main topic a short "##" heading with a few bullet points of
      the key ideas, definitions and results.
    - End with a "## Key terms" list if the material introduces terms.
    - Put the page of important points in brackets, like (p. 4), or
      (notes.pdf, p. 4) when the material comes from several documents.
    - Summarize only what the CONTEXT covers. The student may have chosen certain
      pages on purpose, so don't point out parts of the document that aren't included.
    - Write directly; don't refer to "the context".
    """

QUIZ_TASK = """
    TASK: Write a multiple-choice quiz that checks the student understands the CONTEXT.
    The student answers in the app and it is scored automatically, so every question
    is multiple choice.
    - Write {count} questions unless the student asks for another number (at most {most}).
    - Each question has exactly 4 options and exactly one correct option. Wrong
      options should be plausible to someone who hasn't studied the material, but
      clearly wrong according to the CONTEXT.
    - Test important ideas, not trivia. Every question and answer must be supported
      by the CONTEXT.
    - If the conversation history already has a quiz, ask about different points.
    - "answer" is the exact text of the correct option.
    - "explanation" is one or two sentences, based on the CONTEXT, saying why that
      option is right.
    - "source" and "page" say where in the CONTEXT the answer is found.

    Reply with JSON only, in this shape:
    {{"questions": [{{"type": "multiple_choice", "question": "...", "options": ["...", "...", "...", "..."],
      "answer": "...", "explanation": "...", "source": "notes.pdf", "page": 3}}]}}
    """

FLASHCARDS_TASK = """
    TASK: Write revision flashcards from the CONTEXT.
    - Write {count} cards unless the student asks for another number (at most {most}).
    - "front" is a short question or term; "back" is a brief answer or definition
      (one to three sentences) taken from the CONTEXT.
    - Cover the most important ideas, one idea per card, without repeating cards.
    - "source" and "page" say where in the CONTEXT the answer is found.

    Reply with JSON only, in this shape:
    {{"cards": [{{"front": "...", "back": "...", "source": "notes.pdf", "page": 3}}]}}
    """


def build_study_prompt(task, request, contexts, history=(), topic=None):
    """The prompt for summaries, quizzes and flashcards."""
    return f"""
    You are a helpful study assistant.
    Use only the provided context. Do not invent facts or add outside knowledge.
    If the context is too thin for the task, do what it supports and say what is missing.
    {task}{_focus_line(topic)}{_history_block(history)}
    CONTEXT:
    {_context_block(contexts)}

    STUDENT'S REQUEST:
    {request}
    """


# Backup for the prompt instruction: drop an opening like
# "Based on the provided context, ..." or "According to the context: ...".
_PREAMBLE = re.compile(
    r"^\s*(based on|according to|from|using)\s+(the\s+)?(provided\s+|given\s+)?"
    r"(context|documents?|sources?)(\s+provided)?\s*[,:]\s*",
    re.IGNORECASE,
)


def strip_preamble(text):
    stripped = _PREAMBLE.sub("", text, count=1)
    if stripped == text or not stripped:
        return text
    return stripped[0].upper() + stripped[1:]


# --- Structured output (quizzes and flashcards) -----------------------------

def parse_json(text):
    """Gemini's JSON reply, tolerating a ```json fence around it."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        raise GenerationError("Gemini's reply couldn't be read. Try again.")


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _items(data, key):
    items = data.get(key) if isinstance(data, dict) else data
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _citation(item, contexts):
    """Keep the item's source and page only if they point at material that was sent."""
    try:
        page = int(item.get("page"))
    except (TypeError, ValueError):
        return {"source": None, "page": None}
    sent = {(c["source"], c["page"]) for c in contexts}
    if (item.get("source"), page) in sent:
        return {"source": item["source"], "page": page}
    on_page = {s for s, p in sent if p == page}
    if len(on_page) == 1:  # the model left out or misspelled the file name
        return {"source": on_page.pop(), "page": page}
    return {"source": None, "page": None}


def _option_index(answer, options):
    """The correct option, given its text or its letter ("B", "b)", "C.")."""
    folded = [o.casefold() for o in options]
    if answer.casefold() in folded:
        return folded.index(answer.casefold())
    letter = re.fullmatch(r"\(?([A-Za-z])[).:]?", answer)
    if letter:
        index = ord(letter.group(1).upper()) - ord("A")
        if 0 <= index < len(options):
            return index
    return None


def clean_quiz(data, contexts):
    """Valid multiple-choice questions only: each needs a question, at least two
    different options, and an answer that is one of them. Quizzes are scored in
    the app, so questions without options (short answer) are dropped."""
    questions = []
    for q in _items(data, "questions"):
        question, answer = _text(q.get("question")), _text(q.get("answer"))
        raw = q.get("options") if isinstance(q.get("options"), list) else []
        options = [_text(o) for o in raw if _text(o)]
        if not question or not answer or len({o.casefold() for o in options}) != len(options):
            continue
        index = _option_index(answer, options)
        if len(options) < 2 or index is None:
            continue
        questions.append({
            "type": "multiple_choice",
            "question": question,
            "options": options,
            "answer": options[index],
            "explanation": _text(q.get("explanation")),
            **_citation(q, contexts),
        })
    return questions[:config.QUIZ_MAX_QUESTIONS]


def clean_flashcards(data, contexts):
    cards = []
    for card in _items(data, "cards"):
        front, back = _text(card.get("front")), _text(card.get("back"))
        if front and back:
            cards.append({"front": front, "back": back, **_citation(card, contexts)})
    return cards[:config.FLASHCARDS_MAX]


class _ModelUnavailable(Exception):
    """This model can't answer right now, but another model might."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason  # "busy", "rate_limit", "daily_quota" or "not_found"


_UNAVAILABLE_MESSAGES = {
    "busy": "Gemini is very busy right now. Wait a moment and ask again.",
    "rate_limit": "Gemini's rate limit was reached. Wait a minute and ask again.",
    "daily_quota": "The daily AI quota is used up. Try again tomorrow.",
    "not_found": "The AI service isn't set up correctly. Try again later.",
}


def _generate(model, prompt, json_output=False, attempts=2):
    """One model's attempt: retries a temporary overload once, with a short backoff."""
    import httpx
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    for attempt in range(attempts):
        try:
            response = get_llm().models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    response_mime_type="application/json" if json_output else None,
                ),
            )
            if not response.text:
                raise GenerationError("Gemini returned an empty answer. Try rephrasing your question.")
            return response.text

        except ServerError:
            if attempt == attempts - 1:
                raise _ModelUnavailable("busy")
            time.sleep(2)

        except ClientError as e:
            if e.code == 429:
                raise _ModelUnavailable("daily_quota" if "PerDay" in str(e) else "rate_limit")
            if e.code == 404:
                raise _ModelUnavailable("not_found")
            log.error("Gemini rejected the request (%s)", e.code)
            raise GenerationError("The AI service rejected the request. Try again later.")

        # Not worth trying the fallback models: they would most likely wait as long.
        except httpx.TimeoutException:
            log.warning("Gemini model %s timed out", model)
            raise GenerationError("The AI service took too long to answer. Try again.")
        except httpx.TransportError:
            log.warning("Couldn't reach Gemini", exc_info=True)
            raise GenerationError("Couldn't reach the AI service. Try again in a moment.")


def call_gemini(prompt, json_output=False):
    """Send the prompt to Gemini. With json_output, Gemini is asked for JSON.

    If the main model is overloaded or out of quota, the models in
    GEMINI_FALLBACK_MODELS are tried in order before giving up.
    """
    reasons = set()
    for model in [config.GEMINI_MODEL, *config.GEMINI_FALLBACK_MODELS]:
        try:
            text = _generate(model, prompt, json_output=json_output)
        except _ModelUnavailable as e:
            log.warning("Gemini model %s unavailable (%s)", model, e.reason)
            reasons.add(e.reason)
            continue
        if model != config.GEMINI_MODEL:
            log.info("Answered with fallback model %s", model)
        return text if json_output else strip_preamble(text)

    # Report the most hopeful reason: if any model was only busy or briefly rate
    # limited, asking again soon can work, so don't say "try again tomorrow".
    for reason in ("busy", "rate_limit", "daily_quota", "not_found"):
        if reason in reasons:
            raise GenerationError(_UNAVAILABLE_MESSAGES[reason])


# --- Study modes ----------------------------------------------------------
# Each returns (content, contexts), where contexts are the chunks the content is
# based on. `history` is the conversation so far as [{role, content, mode}], oldest
# first; `where` comes from scope_filter(); `topic` is what the student is focusing
# on, if anything. GenerationError is raised if Gemini fails.

def generate_answer(question, history=(), where=None, topic=None, style=""):
    """Answer a question from the indexed documents (the "ask" mode)."""
    contexts = retrieve_context(retrieval_query(question, history, topic), where=where)
    contexts = with_cited_page(contexts, question, history, where)
    if not contexts:
        return "No relevant information was found.", []

    return call_gemini(build_prompt(question, contexts, history, style, topic)), contexts


def explain(question, history=(), where=None, topic=None):
    """A beginner-friendly explanation, built from a few more passages than an answer."""
    contexts = retrieve_context(retrieval_query(question, history, topic), config.EXPLAIN_TOP_K, where)
    contexts = with_cited_page(contexts, question, history, where)
    if not contexts:
        return "No relevant information was found.", []

    return call_gemini(build_prompt(question, contexts, history, EXPLAIN_STYLE, topic)), contexts


def _study_query(request, topic):
    """What to search for when the scope is too big to use whole. A request the
    student didn't type (just "Summarize") gives nothing to search for."""
    return "\n".join(part for part in (topic, request) if part)


def summarize(request, history=(), where=None, topic=None, typed=True):
    contexts = study_context(_study_query(typed and request, topic), where)
    if not contexts:
        return "No relevant information was found.", []
    return call_gemini(build_study_prompt(SUMMARY_TASK, request, contexts, history, topic)), contexts


def make_quiz(request, history=(), where=None, topic=None, typed=True, count=config.QUIZ_DEFAULT_QUESTIONS):
    """Practice questions, returned as JSON text: {"questions": [...]}."""
    contexts = study_context(_study_query(typed and request, topic), where)
    if not contexts:
        return "No relevant information was found.", []
    task = QUIZ_TASK.format(count=count, most=config.QUIZ_MAX_QUESTIONS)
    reply = call_gemini(build_study_prompt(task, request, contexts, history, topic), json_output=True)
    questions = clean_quiz(parse_json(reply), contexts)
    if not questions:
        raise GenerationError("Gemini couldn't write quiz questions from this material. Try again or pick another topic.")
    return json.dumps({"questions": questions}), contexts


def make_flashcards(request, history=(), where=None, topic=None, typed=True, count=8):
    """Revision flashcards, returned as JSON text: {"cards": [...]}."""
    contexts = study_context(_study_query(typed and request, topic), where)
    if not contexts:
        return "No relevant information was found.", []
    task = FLASHCARDS_TASK.format(count=count, most=config.FLASHCARDS_MAX)
    reply = call_gemini(build_study_prompt(task, request, contexts, history, topic), json_output=True)
    cards = clean_flashcards(parse_json(reply), contexts)
    if not cards:
        raise GenerationError("Gemini couldn't write flashcards from this material. Try again or pick another topic.")
    return json.dumps({"cards": cards}), contexts


def run_mode(mode, request, history=(), where=None, topic=None, typed=True):
    """Run one study mode. `typed` is False when the student sent no text and
    `request` is a default like "Summarize biology.pdf"."""
    if mode == "ask":
        return generate_answer(request, history, where, topic)
    if mode == "explain":
        return explain(request, history, where, topic)
    study = {"summarize": summarize, "quiz": make_quiz, "flashcards": make_flashcards}[mode]
    return study(request, history, where, topic, typed)
