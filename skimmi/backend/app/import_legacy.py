"""One-off: move the data of the pre-authentication version into one user's account.

Before Phase 4A everything was stored for a single, anonymous user in
data/skimmi.db (SQLite) and data/uploads/<doc id>.<ext>. This copies those
documents, conversations, messages and quiz results to the user you name, uploads
the files to storage and re-indexes them with the user's id.

Sign up in the app first, then find your user id in Supabase (Authentication ->
Users) and run, from skimmi/backend:

    python -m app.import_legacy --user-id <your user id> --email you@example.com

It is safe to run again: anything already imported (to any account) is skipped. The legacy files
are left untouched; delete data/skimmi.db and data/uploads/ yourself afterwards.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from . import config, database, rag, storage


def _time(text):
    try:
        value = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return database.now()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def import_legacy(user_id, email="", legacy_db=None, uploads=None, index=True):
    user_id = database.valid_id(user_id)
    if not user_id:
        raise SystemExit("--user-id must be a user's UUID")
    legacy_db = Path(legacy_db or config.LEGACY_DATABASE_FILE)
    uploads = Path(uploads or config.UPLOAD_DIR)
    if not legacy_db.exists():
        raise SystemExit(f"No legacy database at {legacy_db}")

    database.migrate()
    database.ensure_profile(user_id, email)
    old = sqlite3.connect(legacy_db)
    old.row_factory = sqlite3.Row
    store = storage.get_storage()
    counts = {"documents": 0, "conversations": 0, "messages": 0, "quiz_attempts": 0}

    imported_docs = database.all_document_ids()
    with database.begin() as conn:
        imported_chats = {r[0] for r in conn.execute(sa.select(database.conversations.c.id))}

    for doc in old.execute("SELECT * FROM documents ORDER BY uploaded_at"):
        doc = dict(doc)
        if doc["id"] in imported_docs or database.find_document_by_hash(user_id, doc["sha256"]):
            continue
        files = list(uploads.glob(f"{doc['id']}.*"))
        if not files:
            print(f"  skipped {doc['filename']}: its file is missing from {uploads}")
            continue
        extension = files[0].suffix.lower()
        data = files[0].read_bytes()
        path = storage.object_path(user_id, doc["id"], extension)
        store.put(path, data, storage.CONTENT_TYPES[extension])
        database.create_document({
            "id": doc["id"], "user_id": user_id,
            "filename": database.unique_filename(user_id, doc["filename"]),
            "sha256": doc["sha256"], "extension": extension, "size_bytes": len(data), "storage_path": path,
            "subject": doc.get("subject") or "", "course": doc.get("course") or "",
            "pages": doc["pages"], "chunks": doc["chunks"], "index_version": 0,
            "uploaded_at": _time(doc["uploaded_at"]),
        })
        rag.remove_document(doc["id"])  # old chunks have no owner; recover_index rebuilds them
        counts["documents"] += 1

    message_ids = {}  # legacy message id -> new id
    for conversation in old.execute("SELECT * FROM conversations ORDER BY created_at"):
        conversation = dict(conversation)
        if conversation["id"] in imported_chats:
            continue
        with database.begin() as conn:
            conn.execute(database.conversations.insert().values(
                id=conversation["id"], user_id=user_id, title=conversation["title"][:200],
                created_at=_time(conversation["created_at"]), updated_at=_time(conversation["updated_at"]),
            ))
            rows = old.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation["id"],))
            for m in rows:
                m = dict(m)
                new_id = conn.execute(database.messages.insert().values(
                    conversation_id=conversation["id"], user_id=user_id, role=m["role"], content=m["content"],
                    sources=json.loads(m["sources"] or "[]"), mode=m.get("mode") or "ask",
                    scope=(m.get("scope") or "")[:300], created_at=_time(m["created_at"]),
                )).inserted_primary_key[0]
                message_ids[m["id"]] = new_id
                counts["messages"] += 1
        counts["conversations"] += 1

    has_attempts = old.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'quiz_attempts'"
    ).fetchone()
    if has_attempts and counts["conversations"]:
        with database.begin() as conn:
            for a in old.execute("SELECT * FROM quiz_attempts ORDER BY id"):
                a = dict(a)
                conn.execute(database.quiz_attempts.insert().values(
                    user_id=user_id, message_id=message_ids.get(a["message_id"]), title=a["title"][:300],
                    scope=(a["scope"] or "")[:300], correct=a["correct"], total=a["total"],
                    answers=json.loads(a["answers"] or "[]"), completed_at=_time(a["completed_at"]),
                ))
                counts["quiz_attempts"] += 1
    old.close()

    print("Imported " + ", ".join(f"{n} {name}" for name, n in counts.items()))
    if index and counts["documents"]:
        from .main import recover_index

        print("Indexing the imported documents…")
        recover_index()
        with database.begin() as conn:
            rows = conn.execute(sa.select(database.documents.c.filename, database.documents.c.status)
                                .where(database.documents.c.user_id == user_id)).all()
        for filename, status in rows:
            print(f"  {filename}: {status}")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", required=True, help="the Supabase user id to give the data to")
    parser.add_argument("--email", default="", help="the user's email (shown in the account panel)")
    parser.add_argument("--legacy-db", help=f"default: {config.LEGACY_DATABASE_FILE}")
    parser.add_argument("--uploads", help=f"default: {config.UPLOAD_DIR}")
    parser.add_argument("--no-index", action="store_true", help="skip indexing (the backend does it at startup)")
    args = parser.parse_args()
    import_legacy(args.user_id, args.email, args.legacy_db, args.uploads, index=not args.no_index)


if __name__ == "__main__":
    main()
