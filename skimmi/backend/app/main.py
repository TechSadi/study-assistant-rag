"""Skimmi AI API.

Run from skimmi/backend:  uvicorn app.main:app --reload --port 8000

Every route except /api/health needs a signed-in user (auth.current_user). Data
is always looked up by (user id from the verified token, requested id), so an id
that belongs to someone else gives the same 404 as one that doesn't exist.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, database, extract, progress, rag, ratelimit, storage, vectors
from .auth import User, current_user

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger("skimmi")

# Uploads in this process are handled one at a time so duplicate checks and name
# de-duplication can't interleave. Across processes the database's unique
# constraints catch the rare race (see database.DuplicateDocument).
_upload_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    if config.AUTO_MIGRATE:
        database.migrate()
    storage.get_storage().ensure_ready()
    # Load the shared pipeline once at startup so the first request isn't slow.
    rag.get_embedding_model()
    vectors.get_collection()
    if config.RECOVER_ON_STARTUP:
        threading.Thread(target=recover_index, name="recover-index", daemon=True).start()
    log.info("Ready: %d chunks indexed", rag.chunk_count())
    yield


# --- Index recovery -------------------------------------------------------------
# ChromaDB is a cache that can be rebuilt: the files are in storage and the
# metadata in the database. At startup (in the background) recover_index finds
# documents whose chunks are missing (a fresh disk), outdated (INDEX_VERSION
# changed) or never finished (the server stopped mid-upload) and indexes them
# again from their stored file. A document can also be re-indexed on demand
# (POST /api/documents/{id}/reindex). Nothing is re-indexed during normal requests.

@contextmanager
def local_copy(data, extension):
    """The bytes as a temporary file with the right extension (extraction reads files)."""
    handle, path = tempfile.mkstemp(suffix=extension)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        yield Path(path)
    finally:
        Path(path).unlink(missing_ok=True)


def reindex_from_storage(doc):
    """Index a document again from its stored file and mark it ready. A file that is
    missing or can't be read marks the document failed. Storage or vector store
    outages are raised, leaving the document to be tried again later."""
    try:
        data = storage.get_storage().get(doc["storage_path"])
    except storage.NotStored:
        database.mark_document_failed(doc["id"], "The original file is missing. Remove it and upload it again.")
        raise
    try:
        with local_copy(data, doc["extension"]) as path:
            pages, chunks = rag.reindex_document(doc["id"], path, doc["filename"], doc["user_id"])
    except ValueError as e:
        database.mark_document_failed(doc["id"], f"Couldn't read the file. {e}")
        raise
    database.mark_document_ready(doc["id"], pages, chunks, config.INDEX_VERSION)
    log.info("Indexed %s: %d chunks", doc["id"], chunks)


def recover_index():
    """Bring the vector index in line with the database. Safe to run repeatedly."""
    try:
        docs = database.documents_to_check(config.INDEX_VERSION)
        for doc in docs:
            missing = not vectors.has_document(doc["id"])
            if not (doc["outdated"] or missing):
                continue
            if missing and doc["status"] == "ready":
                # Show it as indexing; otherwise old chunks keep working meanwhile.
                database.mark_document_processing(doc["user_id"], doc["id"])
            try:
                reindex_from_storage(doc)
            except (storage.NotStored, ValueError):
                log.warning("Document %s can't be indexed; marked failed", doc["id"])
            except Exception:
                log.warning("Couldn't index document %s now; will retry at next start", doc["id"], exc_info=True)

        # Chunks of documents that no longer exist (e.g. the account was deleted in Supabase).
        known = database.all_document_ids()
        for doc_id in vectors.document_ids() - known:
            if doc_id:
                rag.remove_document(doc_id)
                log.info("Removed orphaned chunks of %s", doc_id)
    except Exception:
        log.exception("Index recovery failed")


# --- App, middleware and errors ---------------------------------------------------

app = FastAPI(title="Skimmi AI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,  # the token travels in a header, not a cookie
    max_age=600,
)

MAX_UPLOAD_BODY = config.MAX_UPLOAD_MB * 1024 * 1024 + 64 * 1024  # file + form overhead


@app.middleware("http")
async def limits_and_logging(request: Request, call_next):
    """Refuse oversized bodies before reading them, add basic security headers,
    and log each request (method, path, status, time; never headers or bodies)."""
    limit = MAX_UPLOAD_BODY if request.url.path == "/api/documents" else config.MAX_JSON_BODY_BYTES
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        return error_response(413, f"The request is too large. Files can be up to {config.MAX_UPLOAD_MB} MB.")

    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Cache-Control", "no-store")
    if request.url.path != "/api/health":
        log.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - started) * 1000)
    return response


ERROR_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 410: "gone", 413: "too_large", 422: "invalid_request", 429: "rate_limited",
    500: "server_error", 503: "unavailable",
}


def error_response(status, detail, headers=None):
    """Every error has the same shape: {"detail": readable message, "code": short slug}."""
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "code": ERROR_CODES.get(status, "error")},
        headers=headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "The request couldn't be completed."
    return error_response(exc.status_code, detail, getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", ())[1:])
    message = first.get("msg", "The request was invalid.")
    return error_response(422, f"{field}: {message}" if field else message)


# Known outages get a clear 503 and nothing about the cause.
@app.exception_handler(storage.StorageError)
async def storage_unavailable(request: Request, exc: Exception):
    return error_response(503, "File storage is unavailable right now. Try again in a moment.")


@app.exception_handler(vectors.VectorStoreError)
async def search_unavailable(request: Request, exc: Exception):
    return error_response(503, "Search is unavailable right now. Try again in a moment.")


async def database_unavailable(request: Request, exc: Exception):
    log.error("Database unavailable on %s: %s", request.url.path, type(exc).__name__)
    return error_response(503, "Skimmi can't reach its database right now. Try again in a moment.")


for _error in database.UNAVAILABLE_ERRORS:
    app.add_exception_handler(_error, database_unavailable)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return error_response(500, "Something went wrong on the server. Try again.")


def limited(name):
    """Dependency: the signed-in user, counted against the `name` rate limit."""
    def dependency(user: User = Depends(current_user)):
        ratelimit.hit(name, user.id)
        return user
    return dependency


# --- Models -----------------------------------------------------------------

class Document(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    uploaded_at: str
    subject: str = ""
    course: str = ""
    status: Literal["processing", "ready", "failed"] = "ready"
    error: str = ""


class DocumentUpdate(BaseModel):
    subject: str = Field("", max_length=200)  # "" removes the subject (and the course)
    course: str = Field("", max_length=200)


class Account(BaseModel):
    id: str
    email: str
    created_at: str
    documents: int
    conversations: int
    quiz_attempts: int


class Source(BaseModel):
    doc_id: str | None = None
    source: str
    page: int
    excerpt: str      # the sentences of the passage that best match the question
    text: str = ""    # the whole passage sent to Gemini (older messages only have `excerpt`)


class AttemptScore(BaseModel):
    correct: int
    total: int
    completed_at: str


class Message(BaseModel):
    id: int
    role: str
    content: str  # markdown, or JSON for quiz and flashcards messages
    sources: list[Source]
    created_at: str
    mode: str = "ask"
    scope: str = ""
    last_attempt: AttemptScore | None = None  # quizzes: the latest completed attempt


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class Conversation(ConversationSummary):
    messages: list[Message]


class ChatRequest(BaseModel):
    # Required for ask and explain. Optional for the other modes, which then
    # cover the whole selected scope ("Summarize notes.pdf").
    question: str = Field("", max_length=2000)
    conversation_id: str | None = Field(None, max_length=64)
    mode: Literal["ask", "explain", "summarize", "quiz", "flashcards"] = "ask"
    doc_id: str | None = Field(None, max_length=64)  # None = all documents (or the subject's)
    subject: str | None = Field(None, max_length=200)  # used when no doc_id is given
    topic: str | None = Field(None, max_length=200)
    page_from: int | None = Field(None, ge=1, le=100_000)  # page range, only with doc_id
    page_to: int | None = Field(None, ge=1, le=100_000)


class ChatResponse(BaseModel):
    conversation: ConversationSummary
    question: Message
    message: Message


class QuizAttemptRequest(BaseModel):
    # The chosen option's index for each question, in order (null for a
    # short-answer question in an older quiz).
    answers: list[int | None] = Field(..., max_length=50)


class QuizAttempt(BaseModel):
    id: int
    message_id: int | None
    conversation_id: str | None
    title: str
    scope: str
    correct: int
    total: int
    completed_at: str


class QuizAttemptResult(QuizAttempt):
    results: list[bool | None]


class Progress(BaseModel):
    quizzes_completed: int
    questions_answered: int
    correct_answers: int
    accuracy: float | None
    activity: dict[str, int]
    recent: list[QuizAttempt]


DEFAULT_REQUESTS = {
    "summarize": "Summarize {target}",
    "quiz": "Quiz me on {target}",
    "flashcards": "Make flashcards for {target}",
}

NOT_FOUND_DOCUMENT = "That document was not found. It may have been removed."


# --- Health and account ---------------------------------------------------------

@app.get("/api/health")
def health():
    """Public: whether the API and its database are up. Reveals nothing else."""
    database.check()
    return {"status": "ok"}


@app.get("/api/me", response_model=Account)
def get_account(user: User = Depends(current_user)):
    account = database.get_account(user.id)
    if not account:
        raise HTTPException(404, "Your account wasn't found. Sign in again.")
    return account


# --- Documents --------------------------------------------------------------

@app.get("/api/documents", response_model=list[Document])
def list_documents(user: User = Depends(current_user)):
    return database.list_documents(user.id)


def check_upload(file):
    """Validates an upload's name, size and content. Returns (filename, extension, bytes)."""
    filename = Path(file.filename or "").name.strip()[:200]
    suffix = Path(filename).suffix.lower()
    kind = extract.file_kind(filename)
    if suffix in extract.OLD_OFFICE_TYPES:
        raise HTTPException(
            400, f"{filename} is an older Office file. Save it as {extract.OLD_OFFICE_TYPES[suffix]} and upload that."
        )
    if not kind or not Path(filename).stem:
        raise HTTPException(
            400, "Only PDF, Word (.docx), PowerPoint (.pptx), EPUB, text and Markdown files can be uploaded."
        )

    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"This file is larger than {config.MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(400, f"{filename} is empty.")
    if not extract.looks_valid(filename, data):
        raise HTTPException(400, f"{filename} isn't a valid {kind} file.")
    return filename, suffix, data


@app.post("/api/documents", response_model=Document, status_code=201)
def upload_document(
    file: UploadFile = File(...), subject: str = Form("", max_length=200), user: User = Depends(limited("upload"))
):
    """Validate, store and index a file. The file is stored before it is indexed,
    so a server that stops mid-way leaves a "processing" document that the
    startup recovery can finish. Anything that fails is cleaned up."""
    filename, suffix, data = check_upload(file)
    sha256 = hashlib.sha256(data).hexdigest()

    with _upload_lock:
        if database.document_count(user.id) >= config.MAX_DOCUMENTS_PER_USER:
            raise HTTPException(
                409, f"You can keep up to {config.MAX_DOCUMENTS_PER_USER} documents. Remove some to add more."
            )
        existing = database.find_document_by_hash(user.id, sha256)
        if existing:
            raise HTTPException(409, f"This file is already in your documents as {existing['filename']}.")

        doc_id = database.new_id()
        display_name = database.unique_filename(user.id, filename)
        path = storage.object_path(user.id, doc_id, suffix)
        try:
            database.create_document({
                "id": doc_id,
                "user_id": user.id,
                "filename": display_name,
                "sha256": sha256,
                "extension": suffix,
                "size_bytes": len(data),
                "storage_path": path,
                "subject": database.existing_spelling(user.id, "subject", database.clean_label(subject)),
            })
        except database.DuplicateDocument:
            raise HTTPException(409, "That file was just uploaded. Refresh your documents.")

    try:
        storage.get_storage().put(path, data, storage.CONTENT_TYPES[suffix])
        try:
            with local_copy(data, suffix) as local:
                pages, chunks = rag.ingest_document(doc_id, local, display_name, user.id)
        except ValueError as e:
            status = 422 if "No text" in str(e) else 400
            raise HTTPException(status, f"Couldn't read {filename}. {e}")
        database.mark_document_ready(doc_id, pages, chunks, config.INDEX_VERSION)
    except BaseException:
        _discard_upload(doc_id, user.id, path)
        raise

    return database.get_document(user.id, doc_id)


def _discard_upload(doc_id, user_id, path):
    """Undo a failed upload: chunks, stored file, then the row. Best effort."""
    for step in (lambda: rag.remove_document(doc_id), lambda: storage.get_storage().delete(path),
                 lambda: database.delete_document(user_id, doc_id)):
        try:
            step()
        except Exception:
            log.warning("Cleanup after a failed upload of %s was incomplete", doc_id, exc_info=True)


@app.patch("/api/documents/{doc_id}", response_model=Document)
def update_document(doc_id: str, update: DocumentUpdate, user: User = Depends(limited("write"))):
    """Assign, change or remove (with an empty subject) a document's subject and course."""
    doc = database.set_document_subject(user.id, doc_id, update.subject, update.course)
    if not doc:
        raise HTTPException(404, NOT_FOUND_DOCUMENT)
    return doc


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: User = Depends(limited("write"))):
    """Removes the document's chunks, stored file and row, in that order, so if a
    step fails the document is still listed and deleting it again finishes the job."""
    doc = database.get_document(user.id, doc_id)
    if not doc:
        raise HTTPException(404, "That document was not found. It may already be removed.")
    rag.remove_document(doc["id"])
    storage.get_storage().delete(doc["storage_path"])
    database.delete_document(user.id, doc["id"])


@app.post("/api/documents/{doc_id}/reindex", response_model=Document)
def reindex_document(doc_id: str, user: User = Depends(limited("upload"))):
    """Index a document again from its stored file (for one that failed or is stuck)."""
    doc = database.get_document(user.id, doc_id)
    if not doc:
        raise HTTPException(404, NOT_FOUND_DOCUMENT)
    database.mark_document_processing(user.id, doc["id"])
    try:
        reindex_from_storage(doc)
    except storage.NotStored:
        raise HTTPException(410, "The original file is missing. Remove the document and upload it again.")
    except ValueError as e:
        raise HTTPException(422, f"Couldn't read {doc['filename']}. {e}")
    except Exception:
        database.mark_document_failed(doc["id"], "Indexing failed. Try again.")
        raise
    return database.get_document(user.id, doc["id"])


@app.get("/api/documents/{doc_id}/file")
def download_document(doc_id: str, user: User = Depends(current_user)):
    """The original file, for its owner only."""
    doc = database.get_document(user.id, doc_id)
    if not doc:
        raise HTTPException(404, NOT_FOUND_DOCUMENT)
    try:
        data = storage.get_storage().get(doc["storage_path"])
    except storage.NotStored:
        raise HTTPException(410, "The original file is missing.")
    safe_name = "".join(c if c.isalnum() or c in " ._()-" else "_" for c in doc["filename"])
    return Response(
        data,
        media_type=storage.CONTENT_TYPES.get(doc["extension"], "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# --- Conversations ----------------------------------------------------------

@app.get("/api/conversations", response_model=list[ConversationSummary])
def list_conversations(user: User = Depends(current_user)):
    return database.list_conversations(user.id)


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(conversation_id: str, user: User = Depends(current_user)):
    conversation = database.get_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(404, "That conversation was not found. It may have been deleted.")
    return conversation


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, user: User = Depends(limited("write"))):
    if not database.delete_conversation(user.id, conversation_id):
        raise HTTPException(404, "That conversation was not found. It may already be deleted.")


def _check_ready(doc):
    if doc["status"] == "processing":
        raise HTTPException(409, f"{doc['filename']} is still being indexed. Try again in a moment.")
    if doc["status"] == "failed":
        raise HTTPException(409, f"{doc['filename']} couldn't be indexed. {doc['error']}".strip())


def resolve_scope(user, request):
    """Check the requested document or subject and pages, which must be the user's.
    Returns (where, label, name): the retrieval filter (always limited to the user),
    a label like "notes.pdf · pages 3–7 · enzymes" shown with the messages, and the
    selected document's or subject's name, if any.

    A document takes precedence over a subject."""
    doc = None
    subject_ids = None
    subject = (request.subject or "").strip()
    if request.doc_id:
        doc = database.get_document(user.id, request.doc_id)
        if not doc:
            raise HTTPException(404, "The selected document was not found. It may have been removed.")
        _check_ready(doc)
    elif subject:
        subject_ids = database.document_ids_for_subject(user.id, subject)
        if not subject_ids:
            raise HTTPException(404, f"No documents are filed under {subject}. Pick another subject.")
        subject = database.existing_spelling(user.id, "subject", subject)

    pages = None
    if request.page_from or request.page_to:
        if not doc:
            raise HTTPException(400, "Pick a document to choose pages from.")
        first = request.page_from or 1
        last = request.page_to or doc["pages"]
        if first > doc["pages"]:
            raise HTTPException(400, f"{doc['filename']} only has {doc['pages']} pages.")
        if first > last:
            raise HTTPException(400, "The first page must come before the last page.")
        pages = (first, min(last, doc["pages"]))

    parts = []
    if doc:
        parts.append(doc["filename"])
    elif subject_ids:
        parts.append(subject)
    if pages:
        parts.append(f"page {pages[0]}" if pages[0] == pages[1] else f"pages {pages[0]}–{pages[1]}")

    where = rag.scope_filter(user.id, doc and doc["id"], pages, subject_ids)
    if parts and rag.scope_size(where) == 0:
        raise HTTPException(422, f"No text was found in {' '.join(parts)}. Try other pages or another document.")

    name = parts[0] if parts else None
    topic = (request.topic or "").strip()
    if topic:
        parts.append(topic)
    return where, " · ".join(parts), name


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user: User = Depends(limited("chat"))):
    """Answer a question, or make a summary, quiz or flashcards, in the chosen mode.

    Retrieval is limited to the user's documents, and to the selected document,
    pages and topic, if any. Without a conversation_id, a new conversation is
    started (titled after the question). The question and answer are saved
    together only once the answer succeeds, so a failed attempt can be retried."""
    question = request.question.strip()
    typed = bool(question)
    if not typed and request.mode not in DEFAULT_REQUESTS:
        raise HTTPException(400, "Type a question first.")

    history = []
    conversation = None
    if request.conversation_id:
        conversation = database.get_conversation(user.id, request.conversation_id)
        if not conversation:
            raise HTTPException(404, "That conversation was not found. Start a new chat.")
        history = database.recent_messages(user.id, conversation["id"], config.HISTORY_MESSAGES)

    if rag.scope_size(rag.scope_filter(user.id)) == 0:
        raise HTTPException(400, "Upload a document before asking a question.")

    where, scope, name = resolve_scope(user, request)
    topic = (request.topic or "").strip() or None
    if not typed:
        question = DEFAULT_REQUESTS[request.mode].format(target=topic or name or "my documents")

    try:
        answer, contexts = rag.run_mode(request.mode, question, history, where, topic, typed)
    except rag.GenerationError as e:
        raise HTTPException(503, str(e))

    # One citation per (document, page), keeping the best-ranked passage. The
    # excerpt is the part of it that best matches what was searched for.
    search = rag.retrieval_query(question, history, topic)
    sources, seen = [], set()
    for c in contexts:
        key = (c["doc_id"], c["page"])
        if key not in seen:
            seen.add(key)
            sources.append({
                "doc_id": c["doc_id"],
                "source": c["source"],
                "page": c["page"],
                "excerpt": rag.best_excerpt(c["text"], search),
                "text": c["text"],
            })

    if request.mode == "quiz" and contexts:
        # Keep what the quiz covered, so "New quiz" can ask for another on the same material.
        quiz = json.loads(answer)
        quiz["request"] = {
            "doc_id": request.doc_id,
            "subject": None if request.doc_id else (request.subject or "").strip() or None,
            "topic": topic,
            "page_from": request.page_from,
            "page_to": request.page_to,
        }
        answer = json.dumps(quiz)

    if conversation is None:
        conversation = database.create_conversation(user.id, database.make_title(question))
    asked, message = database.add_exchange(
        user.id, conversation["id"], question, answer, sources, request.mode, scope
    )

    summary = {k: conversation[k] for k in ("id", "title", "created_at")}
    summary["updated_at"] = message["created_at"]
    return {"conversation": summary, "question": asked, "message": message}


# --- Quizzes and progress ----------------------------------------------------

@app.post("/api/messages/{message_id}/quiz-attempts", response_model=QuizAttemptResult, status_code=201)
def submit_quiz_attempt(message_id: int, attempt: QuizAttemptRequest, user: User = Depends(limited("write"))):
    """Score a completed quiz and record the result for the progress summary.
    The score is computed here from the saved quiz, never taken from the client."""
    message = database.get_message(user.id, message_id)
    questions = message and progress.quiz_questions(message)
    if not questions:
        raise HTTPException(404, "That quiz was not found. Its chat may have been deleted.")
    try:
        score = progress.score_quiz(questions, attempt.answers)
    except progress.ScoringError as e:
        raise HTTPException(400, str(e))

    saved = database.add_quiz_attempt(
        user.id, message, database.question_before(user.id, message), score["correct"], score["total"],
        attempt.answers,
    )
    return {**saved, "results": score["results"]}


@app.get("/api/progress", response_model=Progress)
def get_progress(user: User = Depends(current_user)):
    return progress.summary(user.id)
