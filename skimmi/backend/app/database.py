"""The relational database: users' document metadata, conversations and quiz results.

Supabase PostgreSQL in production (config.DATABASE_URL), SQLite in tests and
offline development, through SQLAlchemy Core. The schema is defined here and
created by the Alembic migrations in migrations/versions.

Every function that reads or changes user data takes the authenticated user's id
and filters on it, so a document, conversation or quiz id belonging to someone
else behaves exactly like one that doesn't exist. The ids never come from the
request body: main.py passes the id from the verified access token (auth.py).

Document chunks and embeddings live in ChromaDB (vectors.py) and the original
files in Supabase Storage (storage.py). Only metadata is kept here.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from . import config


metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

Id = sa.Uuid(as_uuid=False)  # uuid in PostgreSQL, text in SQLite; str in Python
# SQLite only auto-increments an INTEGER PRIMARY KEY.
BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
Json = sa.JSON().with_variant(JSONB(), "postgresql")
Timestamp = sa.DateTime(timezone=True)


def _owner():
    return sa.Column(
        "user_id", Id, sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )


# One row per Supabase Auth user, created on their first request. In PostgreSQL the
# id also references auth.users (added by the migration), so deleting the account
# in Supabase deletes all of the user's rows here.
profiles = sa.Table(
    "profiles",
    metadata,
    sa.Column("id", Id, primary_key=True),
    sa.Column("email", sa.String(320), nullable=False, server_default=""),
    sa.Column("created_at", Timestamp, nullable=False),
    sa.Column("last_seen_at", Timestamp, nullable=False),
)

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", Id, primary_key=True),  # also the doc_id in ChromaDB metadata
    _owner(),
    sa.Column("filename", sa.String(255), nullable=False),  # shown in the UI and citations
    sa.Column("sha256", sa.String(64), nullable=False),     # detects re-uploads of the same file
    sa.Column("extension", sa.String(10), nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("storage_path", sa.String(200), nullable=False, unique=True),  # <user id>/<doc id>.<ext>
    sa.Column("pages", sa.Integer, nullable=False, server_default="0"),
    sa.Column("chunks", sa.Integer, nullable=False, server_default="0"),
    sa.Column("subject", sa.String(60), nullable=False, server_default=""),  # '' = no subject
    sa.Column("course", sa.String(60), nullable=False, server_default=""),
    # processing: being indexed; ready: searchable; failed: the stored file couldn't be indexed.
    sa.Column("status", sa.String(12), nullable=False, server_default="processing"),
    sa.Column("index_version", sa.Integer, nullable=False, server_default="0"),  # config.INDEX_VERSION used
    sa.Column("error", sa.String(300), nullable=False, server_default=""),
    sa.Column("uploaded_at", Timestamp, nullable=False),
    sa.Column("updated_at", Timestamp, nullable=False),
    sa.CheckConstraint("status IN ('processing', 'ready', 'failed')", name="status"),
    sa.UniqueConstraint("user_id", "sha256"),
    sa.Index("ix_documents_user_id_uploaded_at", "user_id", "uploaded_at"),
)
# Names are unique per user regardless of case ("Notes.pdf" becomes "Notes (2).pdf").
sa.Index(
    "uq_documents_user_id_lower_filename",
    documents.c.user_id,
    sa.func.lower(documents.c.filename),
    unique=True,
)

conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("id", Id, primary_key=True),
    _owner(),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("created_at", Timestamp, nullable=False),
    sa.Column("updated_at", Timestamp, nullable=False),
    sa.Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", BigId, primary_key=True, autoincrement=True),
    sa.Column(
        "conversation_id", Id, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    ),
    _owner(),  # the conversation's owner, repeated for Row Level Security and quick filtering
    sa.Column("role", sa.String(10), nullable=False),
    sa.Column("content", sa.Text, nullable=False),  # markdown, or JSON for quizzes and flashcards
    sa.Column("sources", Json, nullable=False),     # list of citations
    sa.Column("mode", sa.String(12), nullable=False, server_default="ask"),
    sa.Column("scope", sa.String(300), nullable=False, server_default=""),  # e.g. "notes.pdf · pages 3–7"
    sa.Column("created_at", Timestamp, nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant')", name="role"),
    sa.Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    sa.Index("ix_messages_user_id_role_mode", "user_id", "role", "mode"),
)

# One row per completed quiz. Kept when the chat is deleted, so progress isn't lost.
quiz_attempts = sa.Table(
    "quiz_attempts",
    metadata,
    sa.Column("id", BigId, primary_key=True, autoincrement=True),
    _owner(),
    sa.Column("message_id", BigId, sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
    sa.Column("title", sa.String(300), nullable=False),  # the request, e.g. "Quiz me on enzymes"
    sa.Column("scope", sa.String(300), nullable=False, server_default=""),
    sa.Column("correct", sa.Integer, nullable=False),
    sa.Column("total", sa.Integer, nullable=False),   # questions answered and scored
    sa.Column("answers", Json, nullable=False),       # chosen option index per question
    sa.Column("completed_at", Timestamp, nullable=False),
    sa.CheckConstraint("correct >= 0 AND correct <= total", name="score"),
    sa.Index("ix_quiz_attempts_user_id_id", "user_id", "id"),
    sa.Index("ix_quiz_attempts_message_id", "message_id"),
)


# --- Connection ---------------------------------------------------------------

def database_url(url=None):
    """Supabase shows postgresql:// URLs; use the psycopg 3 driver for them."""
    url = url or config.DATABASE_URL
    return re.sub(r"^postgres(?:ql)?://", "postgresql+psycopg://", url)


@lru_cache(maxsize=1)
def get_engine():
    url = database_url()
    if url.startswith("sqlite"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = sa.create_engine(url, connect_args={"check_same_thread": False})

        @sa.event.listens_for(engine, "connect")
        def _foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys = ON")

        return engine
    return sa.create_engine(
        url,
        pool_pre_ping=True,   # drop connections the pooler closed
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        pool_recycle=300,
        connect_args={
            "connect_timeout": 10,
            # Supabase's transaction pooler (port 6543) can't use prepared statements.
            "prepare_threshold": None,
            "options": "-c statement_timeout=15000",
        },
    )


# Errors that mean the database couldn't be reached (rather than a bad query).
# main.py answers these with 503.
UNAVAILABLE_ERRORS = (sa.exc.OperationalError, sa.exc.InterfaceError, sa.exc.TimeoutError)


def begin():
    """`with begin() as conn:` one short transaction, committed on success."""
    return get_engine().begin()


def include_in_autogenerate(obj, name, type_, reflected, compare_to):
    """Alembic autogenerate hook: ignore the profiles -> auth.users foreign key,
    which only exists on Supabase and is managed by the migration itself."""
    return not (type_ == "foreign_key_constraint" and obj.referred_table.schema == "auth")


def migrate():
    """Apply pending Alembic migrations (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(config.BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logging"] = False
    command.upgrade(cfg, "head")


def check():
    """Raises one of UNAVAILABLE_ERRORS if the database can't be reached."""
    with begin() as conn:
        conn.execute(sa.text("SELECT 1"))


def now():
    return datetime.now(timezone.utc)


def iso(value):
    """Timestamps are sent to the frontend as ISO strings (SQLite drops the time zone)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def new_id():
    return str(uuid.uuid4())


def valid_id(value):
    """The id as a canonical UUID string, or None. PostgreSQL rejects malformed
    UUIDs with an error, so they are treated as "not found" before querying."""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _row(row, *timestamps):
    if row is None:
        return None
    data = dict(row._mapping)
    for key in timestamps:
        data[key] = iso(data[key])
    return data


# --- Users --------------------------------------------------------------------

def ensure_profile(user_id, email=""):
    """Create the user's profile row on their first request; update last seen."""
    stamp = now()
    with begin() as conn:
        updated = conn.execute(
            profiles.update().where(profiles.c.id == user_id).values(email=email or "", last_seen_at=stamp)
        ).rowcount
        if not updated:
            try:
                with conn.begin_nested():
                    conn.execute(
                        profiles.insert().values(id=user_id, email=email or "", created_at=stamp, last_seen_at=stamp)
                    )
            except sa.exc.IntegrityError:
                pass  # created by a concurrent request


def get_account(user_id):
    """The profile with counts of what the user has stored, or None."""
    with begin() as conn:
        profile = conn.execute(sa.select(profiles).where(profiles.c.id == user_id)).first()
        if profile is None:
            return None

        def count(table):
            return conn.execute(
                sa.select(sa.func.count()).select_from(table).where(table.c.user_id == user_id)
            ).scalar_one()

        return {
            **_row(profile, "created_at", "last_seen_at"),
            "documents": count(documents),
            "conversations": count(conversations),
            "quiz_attempts": count(quiz_attempts),
        }


# --- Documents ------------------------------------------------------------------

DOCUMENT_TIMESTAMPS = ("uploaded_at", "updated_at")


def list_documents(user_id):
    with begin() as conn:
        rows = conn.execute(
            sa.select(documents).where(documents.c.user_id == user_id).order_by(documents.c.uploaded_at)
        )
        return [_row(r, *DOCUMENT_TIMESTAMPS) for r in rows]


def get_document(user_id, doc_id):
    doc_id = valid_id(doc_id)
    if not doc_id:
        return None
    with begin() as conn:
        row = conn.execute(
            sa.select(documents).where(documents.c.id == doc_id, documents.c.user_id == user_id)
        ).first()
    return _row(row, *DOCUMENT_TIMESTAMPS)


def document_count(user_id):
    with begin() as conn:
        return conn.execute(
            sa.select(sa.func.count()).select_from(documents).where(documents.c.user_id == user_id)
        ).scalar_one()


def find_document_by_hash(user_id, sha256):
    with begin() as conn:
        row = conn.execute(
            sa.select(documents).where(documents.c.user_id == user_id, documents.c.sha256 == sha256)
        ).first()
    return _row(row, *DOCUMENT_TIMESTAMPS)


def unique_filename(user_id, filename):
    """Different files may share a name. Later ones become "notes (2).pdf",
    "notes (3).pdf", ... so the sidebar and citations stay unambiguous."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    with begin() as conn:
        taken = {
            r[0].lower()
            for r in conn.execute(sa.select(documents.c.filename).where(documents.c.user_id == user_id))
        }
    candidate, n = filename, 2
    while candidate.lower() in taken:
        candidate = f"{stem} ({n}){suffix}"
        n += 1
    return candidate


class DuplicateDocument(Exception):
    """Another upload with the same file or name was saved at the same moment."""


def create_document(doc):
    """Insert a new document in the "processing" state. `doc` has id, user_id,
    filename, sha256, extension, size_bytes, storage_path, subject."""
    stamp = now()
    row = {"course": "", "status": "processing", "uploaded_at": stamp, "updated_at": stamp, **doc}
    try:
        with begin() as conn:
            conn.execute(documents.insert().values(**row))
    except sa.exc.IntegrityError as e:
        raise DuplicateDocument() from e
    return get_document(doc["user_id"], doc["id"])


def mark_document_ready(doc_id, pages, chunks, version):
    with begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == doc_id)
            .values(status="ready", pages=pages, chunks=chunks, index_version=version, error="", updated_at=now())
        )


def mark_document_failed(doc_id, error):
    with begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == doc_id)
            .values(status="failed", error=error[:300], updated_at=now())
        )


def mark_document_processing(user_id, doc_id):
    with begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == doc_id, documents.c.user_id == user_id)
            .values(status="processing", error="", updated_at=now())
        )


def documents_to_check(version):
    """Every document that may need (re-)indexing, across all users: those not ready,
    or indexed with an older pipeline. Ready, current documents are returned too,
    flagged, so recovery can confirm their chunks still exist. Only used by the
    startup recovery (main.recover_index), never by a request."""
    with begin() as conn:
        rows = conn.execute(
            sa.select(documents).where(documents.c.status != "failed").order_by(documents.c.uploaded_at)
        )
        docs = [_row(r, *DOCUMENT_TIMESTAMPS) for r in rows]
    for doc in docs:
        doc["outdated"] = doc["status"] != "ready" or doc["index_version"] < version
    return docs


def all_document_ids():
    """Every document id, across all users (startup recovery only)."""
    with begin() as conn:
        return {r[0] for r in conn.execute(sa.select(documents.c.id))}


def clean_label(text):
    """A subject or course name with its spacing tidied."""
    return re.sub(r"\s+", " ", text or "").strip()[:config.SUBJECT_MAX_CHARS]


def existing_spelling(user_id, column, name):
    """Reuse the spelling already in use ("biology" -> "Biology"), so one subject
    isn't split into two groups by capitalization. Only the user's own documents count."""
    if not name:
        return ""
    col = documents.c[column]
    with begin() as conn:
        row = conn.execute(
            sa.select(col)
            .where(documents.c.user_id == user_id, sa.func.lower(col) == name.lower())
            .order_by(documents.c.uploaded_at)
            .limit(1)
        ).first()
    return row[0] if row else name


def set_document_subject(user_id, doc_id, subject, course=""):
    """Assign a subject and optional course. An empty subject removes both.
    Returns the updated document, or None if the user has no such document."""
    if not get_document(user_id, doc_id):
        return None
    subject = existing_spelling(user_id, "subject", clean_label(subject))
    course = existing_spelling(user_id, "course", clean_label(course)) if subject else ""
    with begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == doc_id, documents.c.user_id == user_id)
            .values(subject=subject, course=course, updated_at=now())
        )
    return get_document(user_id, doc_id)


def document_ids_for_subject(user_id, subject):
    with begin() as conn:
        rows = conn.execute(
            sa.select(documents.c.id).where(
                documents.c.user_id == user_id,
                documents.c.subject != "",
                sa.func.lower(documents.c.subject) == subject.lower(),
            )
        )
        return [r[0] for r in rows]


def delete_document(user_id, doc_id):
    with begin() as conn:
        return conn.execute(
            documents.delete().where(documents.c.id == doc_id, documents.c.user_id == user_id)
        ).rowcount > 0


# --- Conversations ----------------------------------------------------------------

MESSAGE_COLUMNS = [
    messages.c.id, messages.c.role, messages.c.content, messages.c.sources,
    messages.c.created_at, messages.c.mode, messages.c.scope,
]
CONVERSATION_COLUMNS = [
    conversations.c.id, conversations.c.title, conversations.c.created_at, conversations.c.updated_at,
]


def make_title(question, max_length=60):
    """Title a conversation after its first question, cut at a word boundary."""
    text = re.sub(r"\s+", " ", question).strip()
    if len(text) <= max_length:
        return text
    cut = text[:max_length].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:?!") + "…"


def list_conversations(user_id):
    with begin() as conn:
        rows = conn.execute(
            sa.select(*CONVERSATION_COLUMNS)
            .where(conversations.c.user_id == user_id)
            .order_by(conversations.c.updated_at.desc())
        )
        return [_row(r, "created_at", "updated_at") for r in rows]


def get_conversation(user_id, conversation_id):
    """The user's conversation with all its messages, or None."""
    conversation_id = valid_id(conversation_id)
    if not conversation_id:
        return None
    with begin() as conn:
        row = conn.execute(
            sa.select(*CONVERSATION_COLUMNS).where(
                conversations.c.id == conversation_id, conversations.c.user_id == user_id
            )
        ).first()
        if not row:
            return None
        found = [
            _message(m)
            for m in conn.execute(
                sa.select(*MESSAGE_COLUMNS)
                .where(messages.c.conversation_id == conversation_id, messages.c.user_id == user_id)
                .order_by(messages.c.id)
            )
        ]
        # Each quiz shows the score of its latest attempt.
        attempts = conn.execute(
            sa.select(quiz_attempts.c.message_id, quiz_attempts.c.correct, quiz_attempts.c.total,
                      quiz_attempts.c.completed_at)
            .join(messages, messages.c.id == quiz_attempts.c.message_id)
            .where(messages.c.conversation_id == conversation_id, quiz_attempts.c.user_id == user_id)
            .order_by(quiz_attempts.c.id)
        )
        latest = {
            a.message_id: {"correct": a.correct, "total": a.total, "completed_at": iso(a.completed_at)}
            for a in attempts
        }
    for m in found:
        m["last_attempt"] = latest.get(m["id"])
    return {**_row(row, "created_at", "updated_at"), "messages": found}


def recent_messages(user_id, conversation_id, limit):
    """The last `limit` messages, oldest first, as [{role, content, mode}]."""
    with begin() as conn:
        rows = conn.execute(
            sa.select(messages.c.role, messages.c.content, messages.c.mode)
            .where(messages.c.conversation_id == conversation_id, messages.c.user_id == user_id)
            .order_by(messages.c.id.desc())
            .limit(limit)
        ).all()
    return [dict(r._mapping) for r in reversed(rows)]


def create_conversation(user_id, title):
    stamp = now()
    conversation = {"id": new_id(), "user_id": user_id, "title": title[:200], "created_at": stamp, "updated_at": stamp}
    with begin() as conn:
        conn.execute(conversations.insert().values(**conversation))
    return {"id": conversation["id"], "title": conversation["title"],
            "created_at": iso(stamp), "updated_at": iso(stamp)}


def add_exchange(user_id, conversation_id, question, answer, sources, mode="ask", scope=""):
    """Store a question and its answer together. Both are tagged with the study mode
    and scope. Returns (user message, assistant message)."""
    stamp = now()
    common = {"conversation_id": conversation_id, "user_id": user_id, "created_at": stamp,
              "mode": mode, "scope": scope[:300]}
    with begin() as conn:
        asked = conn.execute(
            messages.insert().values(role="user", content=question, sources=[], **common)
        ).inserted_primary_key[0]
        answered = conn.execute(
            messages.insert().values(role="assistant", content=answer, sources=sources, **common)
        ).inserted_primary_key[0]
        conn.execute(
            conversations.update()
            .where(conversations.c.id == conversation_id, conversations.c.user_id == user_id)
            .values(updated_at=stamp)
        )
        rows = conn.execute(
            sa.select(*MESSAGE_COLUMNS).where(messages.c.id.in_([asked, answered])).order_by(messages.c.id)
        ).all()
    return tuple(_message(r) for r in rows)


def get_message(user_id, message_id):
    """One of the user's messages, with its conversation id, or None."""
    with begin() as conn:
        row = conn.execute(
            sa.select(*MESSAGE_COLUMNS, messages.c.conversation_id).where(
                messages.c.id == message_id, messages.c.user_id == user_id
            )
        ).first()
    return _message(row) if row else None


def question_before(user_id, message):
    """The user message that asked for this answer."""
    with begin() as conn:
        row = conn.execute(
            sa.select(messages.c.content)
            .where(
                messages.c.conversation_id == message["conversation_id"],
                messages.c.user_id == user_id,
                messages.c.id < message["id"],
                messages.c.role == "user",
            )
            .order_by(messages.c.id.desc())
            .limit(1)
        ).first()
    return row[0] if row else ""


def delete_conversation(user_id, conversation_id):
    """Deletes the conversation and its messages. Returns False if the user has no such conversation."""
    conversation_id = valid_id(conversation_id)
    if not conversation_id:
        return False
    with begin() as conn:
        return conn.execute(
            conversations.delete().where(
                conversations.c.id == conversation_id, conversations.c.user_id == user_id
            )
        ).rowcount > 0


def _message(row):
    m = dict(row._mapping)
    m["created_at"] = iso(m["created_at"])
    if isinstance(m["sources"], str):  # stored as text by some drivers
        m["sources"] = json.loads(m["sources"])
    return m


# --- Quiz results and progress ------------------------------------------------------

def _attempt_query():
    return sa.select(
        quiz_attempts.c.id, quiz_attempts.c.message_id, messages.c.conversation_id,
        quiz_attempts.c.title, quiz_attempts.c.scope, quiz_attempts.c.correct,
        quiz_attempts.c.total, quiz_attempts.c.completed_at,
    ).select_from(quiz_attempts.outerjoin(messages, messages.c.id == quiz_attempts.c.message_id))


def add_quiz_attempt(user_id, message, title, correct, total, answers):
    """Record a completed quiz. Returns the saved attempt."""
    with begin() as conn:
        attempt_id = conn.execute(
            quiz_attempts.insert().values(
                user_id=user_id, message_id=message["id"], title=title[:300], scope=message["scope"],
                correct=correct, total=total, answers=answers, completed_at=now(),
            )
        ).inserted_primary_key[0]
        row = conn.execute(_attempt_query().where(quiz_attempts.c.id == attempt_id)).first()
    return _row(row, "completed_at")


def quiz_totals(user_id):
    """(quizzes completed, questions answered, correct answers)."""
    with begin() as conn:
        row = conn.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(quiz_attempts.c.total), 0),
                sa.func.coalesce(sa.func.sum(quiz_attempts.c.correct), 0),
            ).where(quiz_attempts.c.user_id == user_id)
        ).first()
    return tuple(int(v) for v in row)


def recent_quiz_attempts(user_id, limit):
    """The latest attempts, newest first. conversation_id is None if the chat was deleted."""
    with begin() as conn:
        rows = conn.execute(
            _attempt_query().where(quiz_attempts.c.user_id == user_id).order_by(quiz_attempts.c.id.desc()).limit(limit)
        )
        return [_row(r, "completed_at") for r in rows]


def study_activity(user_id):
    """How many requests were made in each study mode, across the user's saved chats."""
    with begin() as conn:
        rows = conn.execute(
            sa.select(messages.c.mode, sa.func.count())
            .where(messages.c.user_id == user_id, messages.c.role == "user")
            .group_by(messages.c.mode)
        )
        return {mode: count for mode, count in rows}
