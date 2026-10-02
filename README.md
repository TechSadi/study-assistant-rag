# Skimmi AI

Skimmi AI is a web-based study assistant for your PDFs. Upload lecture notes, textbook chapters or readings, then ask questions, get explanations and summaries, take scored multiple-choice quizzes or revise with flashcards. Everything is grounded in those documents and cites the filename and page it came from, with the supporting passages one click away. You can file documents by subject and course, study one subject, one document, a page range or a topic, and track your quiz results over time. Conversations are saved so you can come back and ask follow-up questions.

Each student has their own account (Supabase Auth). Documents, chats, quizzes and progress are private to the account that created them.

It is a React + Tailwind frontend and a FastAPI backend, both in `skimmi/`, using Supabase for sign-in, PostgreSQL and private file storage.

## How it works

One RAG pipeline (`skimmi/backend/app/rag.py`) handles every document and question:

1. **Extract** text from each PDF page with PyMuPDF (no OCR, so scanned PDFs are rejected). Text is read as blocks, so each paragraph, heading or table row stays in one piece. Lines that only wrapped are joined, and words hyphenated across lines are rejoined.
2. **Chunk** each page into pieces of up to 800 characters built from whole sentences. A paragraph that fits in a chunk is kept together, and a heading stays with the paragraph it introduces. Each chunk repeats the last sentences (up to 150 characters) of the one before. Chunks never cross pages, so every chunk has exactly one page number. Only a single "sentence" longer than a chunk (a big table, say) is cut mid-way.
3. **Embed** chunks with Sentence Transformers (`all-MiniLM-L6-v2`) and store them in ChromaDB, tagged with the owner's user id, the document's UUID, filename and page. Documents are added and removed individually; the index is never rebuilt as a whole. Every search is filtered by the signed-in user's id.
4. **Retrieve** up to 4 of the most similar chunks for a question. Chunks much further from the question than the best match (more than 0.15 cosine distance) are left out, so weak matches don't dilute the context, and duplicate passages are sent once. For a follow-up, the search also uses the previous question, so "explain the second one" finds the right passages.
5. **Generate** an answer with Gemini. The prompt tells it to use only the retrieved context and to say when the context doesn't contain the answer. For follow-ups it also receives the last 3 question/answer pairs, labelled as context for understanding the question, not as a source of facts.

The embedding model and the ChromaDB connection are created once when the backend starts and reused for every request.

Each answer's sources list the document and page of every passage sent to Gemini, with a short excerpt: the sentences of that passage that best match the question (up to about 320 characters). The whole passage is kept too, so it can be expanded.

ChromaDB is treated as a rebuildable cache: the original files are in Supabase Storage and their metadata in PostgreSQL. See [Index recovery](#index-recovery).

### Study modes

Every mode uses the same retrieval and the same Gemini call. Only the amount of material and the prompt differ:

| Mode | Material sent to Gemini | Output |
|---|---|---|
| **Ask** | Up to 4 of the most relevant chunks | A direct answer (the original question answering) |
| **Explain** | Up to 5 of the most relevant chunks | A beginner-friendly, step-by-step explanation. Any example Skimmi makes up itself is marked as its own |
| **Summarize** | Up to 16 chunks from the selected scope, in page order | Markdown with an overview, headings, key points, key terms and page references |
| **Quiz** | Same as Summarize | JSON: multiple-choice questions (4 options, one correct), each with an answer, a short explanation and the page it came from. Answered and scored in the app |
| **Flashcards** | Same as Summarize | JSON: question/answer cards, each with the page it came from |

For Summarize, Quiz and Flashcards, a scope of 16 chunks or fewer (a short document or a page range) is sent whole. A larger scope is narrowed by semantic search on the topic and the student's request. If there is nothing to search for (just "Summarize"), chunks are sampled evenly across the scope, so the result covers the whole document and not only its first pages.

The backend checks quiz and flashcard JSON before saving it. Malformed questions are dropped (including questions without options, or with the same option twice), a multiple-choice answer given as a letter ("B") is matched to its option, and a cited page is kept only if that page was actually sent to Gemini. If nothing usable is left, the request fails with a message and nothing is saved. A quiz asked for in a chat that already has one is told to cover different points.

### Subject, document, page and topic scope

- **Subject:** retrieval is limited to the documents filed under that subject (a ChromaDB filter `doc_id $in [...]`). The subject lives in the database, not in the chunks, so it can be changed without re-indexing.
- **Document:** retrieval is limited to that document's chunks (a ChromaDB `where` filter on `doc_id`). A document takes precedence over a subject. With neither selected, all documents are searched.
- **Pages:** when a document is selected, a page range acts as a chapter or section selector. It adds a `page` filter to the same query, so a range with no text returns a clear error instead of an answer.
- **Topic or chapter:** free text that is added to the search query and given to Gemini as the student's focus. It steers retrieval rather than filtering strictly.

## Learning features

### Interactive quizzes

A quiz is shown one question at a time. Choose an option and press **Check answer**: the correct option turns green, a wrong choice turns red, and a short explanation from the document appears with the page it came from (or a note if Gemini didn't give a usable page). **Next question** moves on, and after the last one the results show the score, a percentage and a review of every question with what you chose, the right answer and the page to revisit.

- **Retry quiz** starts the same questions again.
- **New quiz** asks for another quiz on the same material (the same subject, document, pages and topic), in the same chat.
- The score is saved when you reach the results. Reopening the chat later shows the quiz's latest score next to its title.

Scoring is done by the backend (`POST /api/messages/{id}/quiz-attempts`), which checks the chosen options against the saved quiz. Quizzes saved before Phase 3 can contain short-answer questions: these are shown with a **Show model answer** button and left out of the score.

### Study progress

The **Study progress** card at the top of the sidebar shows the number of quizzes completed, questions answered, correct answers and overall accuracy, plus how many questions, summaries and flashcard sets you've asked for. **Recent quizzes** lists the last five results; click one to reopen its chat. Quiz results are kept when their chat is deleted, so your totals don't drop (the activity counts only cover saved chats).

### Subjects and courses

Each document can have a subject (for example "Biology") and an optional course (for example "BIO 101"). Use the tag button on a document to add, change or remove them; existing names are suggested, and "biology" is matched to an existing "Biology". Once any document has a subject:

- the sidebar groups documents by subject (documents without one are listed under **No subject**), shows each document's course, and has filter chips to show one subject;
- uploading while a subject filter is selected files the new PDFs under that subject;
- the study picker above the message box offers **All of <subject>** as well as each document, so you can ask, summarize or quiz across a whole subject.

## Prerequisites

- Python 3.11+ (developed on 3.13)
- Node.js 20+ (developed on 24)
- A Gemini API key
- A Supabase project (the free tier is enough)

## Setup

### 1. Create the Supabase project

1. Create a project at https://supabase.com.
2. **Authentication → Providers → Email:** keep email sign-up on, keep **Confirm email** on, and set the minimum password length to 8.
3. **Authentication → URL Configuration:** set **Site URL** to `http://localhost:5173` and add `http://localhost:5173/**` to the redirect URLs. Confirmation and password-reset links return there.
4. Collect these values:
   - **Project Settings → API:** the project URL, the **anon / publishable** key (for the frontend) and the **service_role / secret** key (backend only).
   - **Connect → Session pooler:** the PostgreSQL connection string, with your database password filled in.
   - **Project Settings → JWT Keys:** if the project still uses the *legacy JWT secret*, copy it too. Projects on the newer signing keys don't need it: the backend verifies tokens with the project's public keys.

You don't need to create tables or the storage bucket. The backend applies the database migrations (including Row Level Security) and creates the private `documents` bucket when it starts.

### 2. Configure the backend

```bash
cd skimmi/backend
cp .env.example .env        # then fill it in
```

Only the backend reads `.env`, and `.env` files are ignored by git.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Gemini API key |
| `SUPABASE_URL` | (required) | `https://<ref>.supabase.co`. Used to verify sign-in tokens and to reach Storage |
| `SUPABASE_SERVICE_ROLE_KEY` | (required) | Server-only key for the private Storage bucket. Never put it in the frontend |
| `DATABASE_URL` | local SQLite file | Supabase PostgreSQL connection string (session pooler). Without it, `data/skimmi-dev.db` is used, which is only for development |
| `SUPABASE_JWT_SECRET` | (optional) | Only for projects still on the legacy shared JWT secret |
| `SKIMMI_STORAGE_BACKEND` | `supabase` | `local` keeps files in `data/uploads` (offline development and tests) |
| `SKIMMI_STORAGE_BUCKET` | `documents` | The private bucket's name |
| `SKIMMI_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Frontend origins allowed to call the API directly. Never `*` |
| `SKIMMI_MAX_UPLOAD_MB` | `25` | Largest file accepted |
| `SKIMMI_MAX_DOCUMENTS` | `100` | Documents per user |
| `SKIMMI_CHAT_PER_MINUTE` | `15` | AI requests per user per minute |
| `SKIMMI_UPLOADS_PER_HOUR` | `30` | Uploads (and re-index requests) per user per hour |
| `SKIMMI_HTTP_TIMEOUT` / `SKIMMI_GEMINI_TIMEOUT` | `30` / `60` | Seconds before a Supabase or Gemini request is abandoned |
| `SKIMMI_AUTO_MIGRATE` | `true` | Apply Alembic migrations at startup |
| `SKIMMI_RECOVER_ON_STARTUP` | `true` | Rebuild missing or outdated vector indexes from storage at startup |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model used for answers |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest` | Tried in order when the main model is busy or out of quota |
| `HF_TOKEN` | (optional) | Hugging Face token for downloading the embedding model |
| `SKIMMI_DATA_DIR` | `data` | Local data (ChromaDB, local uploads, the dev database), relative to `skimmi/backend` |

### 3. Configure the frontend

```bash
cd skimmi/frontend
cp .env.example .env        # set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
```

These two values are public by design: every table has Row Level Security and all data goes through the backend.

### 4. Start the backend

```bash
cd skimmi/backend
python -m venv .venv                 # optional but recommended
.venv\Scripts\activate               # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

On start the backend applies pending migrations, checks the storage bucket, loads the embedding model (about 90 MB the first time) and, in the background, re-indexes any documents that need it. The interactive API docs are turned off, because every data route needs a token.

### 5. Start the frontend

In a second terminal:

```bash
cd skimmi/frontend
npm install
npm run dev
```

Open http://localhost:5173 and create an account. The Vite dev server forwards `/api` requests to the backend on port 8000, so both must be running.

### Moving data from the single-user version

Before accounts existed, everything was stored in `data/skimmi.db` and `data/uploads/`. To give that data to your account, sign up, copy your user id from Supabase (**Authentication → Users**) and run:

```bash
cd skimmi/backend
python -m app.import_legacy --user-id <your user id> --email you@example.com
```

It uploads the files to storage, copies the documents, chats, messages and quiz results, and re-indexes the documents under your account. Running it again skips anything already imported. Delete `data/skimmi.db` and `data/uploads/` afterwards.

## Using the app

- **Sign in:** create an account with your email, confirm it from the email Supabase sends, then sign in. You stay signed in across reloads. **Forgot password?** emails a reset link. The account panel (your email at the bottom of the sidebar) shows your account details and lets you change your password or sign out.
- **Upload:** click **Upload document** or drop files anywhere on the page. Each file is indexed in a few seconds. A document that is still being indexed shows **Indexing…**; one that couldn't be indexed shows **Retry**. The download button fetches the original file.
- **Pick a mode:** the bar above the message box switches between **Ask**, **Explain**, **Summarize**, **Quiz** and **Flashcards**. The selected mode is highlighted, and the box's placeholder and hint describe it.
- **Organize:** use the tag button on a document to give it a subject and course. Filter the list with the subject chips.
- **Pick what to study:** choose a subject or a document from the dropdown (or leave **All documents**). With a document selected you can enter a page range, for example pages 12–30 for one chapter. **Topic or chapter** narrows things further ("enzymes", "Chapter 3: Hashing"). **Clear** resets them all.
- **Ask / Explain:** type a question, or pick a suggestion. Each answer lists the pages it used. Click a page, or **show excerpts**, to read the retrieved passages; **Show the whole passage** expands one. These are the passages the answer was based on, a guide for checking it, not proof that every sentence is right.
- **Summarize / Quiz / Flashcards:** press Enter with the box empty to cover the whole selection, or type what you want ("8 multiple-choice questions", "only the key formulas").
  - **Quizzes** are answered one question at a time with instant feedback, then scored (see [Interactive quizzes](#interactive-quizzes)).
  - **Flashcards** are shown as a grid. Tap a card to flip it to its answer, and tap again to hide it.
- **Follow up:** keep asking in the same chat, in any mode. Skimmi understands references like "it" or "question 3" from earlier messages, including quizzes and flashcards, but still answers only from your documents. Each of your messages shows the mode and material it was sent with.
- **Chats:** every chat is saved and listed under **Chats** in the sidebar, titled after its first question. **New chat** starts a fresh one. The open chat is kept in the URL, so refreshing the page reopens it.
- **Delete:** use the bin icons to remove a document (its file and all its chunks) or a chat (all its messages).

### Documents with the same name

Each document has its own UUID, so different files can share a name. A second `notes.pdf` is listed as `notes (2).pdf`, and citations use that name so they stay unambiguous. Uploading the exact same file twice is refused with a message naming the existing copy. Names and duplicates are checked per account, so two students can upload the same file.

## Security and privacy

- **Sign-in** is handled by Supabase Auth (passwords are never seen or stored by Skimmi). The frontend sends the session's access token with every request.
- **The backend verifies every token itself** (`app/auth.py`): signature (the project's public keys, or the legacy secret), expiry, audience, issuer and role. The user id comes only from the verified token. Ids in URLs or request bodies are always looked up together with that user id, so another user's document, chat or quiz answers with the same 404 as one that doesn't exist.
- **Retrieval is per user:** every chunk carries its owner's `user_id`, and `rag.scope_filter` always adds it to the search. Searches without it are refused (`rag._check_scope`).
- **Row Level Security** is enabled on every table (`migrations/versions/0001_initial.py`). A signed-in user can only read their own rows through Supabase's Data API, and nobody can write through it; all writes go through the backend. The anon key sees nothing.
- **Files** are in a private bucket at `<user id>/<document id>.<ext>`, reached only by the backend with the service role key after an ownership check. The user's file name never becomes a path.
- **Uploads** are checked for type, size (`SKIMMI_MAX_UPLOAD_MB`), empty files and content (the bytes must match the extension). Oversized requests are refused before they are read. Duplicates are refused per account, and each account has a document limit.
- **Rate limits** (in memory, per user): AI requests, uploads and other changes. Requests with a missing or invalid token are limited per address. Sign-in attempts themselves are rate-limited by Supabase Auth.
- **Errors** have one shape, `{"detail": "readable message", "code": "slug"}`. Stack traces, database errors and configuration details are only logged, never returned. Logs record method, path, status and time, never tokens, passwords or document text.
- **CORS** allows only the origins in `SKIMMI_CORS_ORIGINS`, and only the `Authorization` and `Content-Type` headers.

## Index recovery

Free hosting disks are often wiped on restart, so the vector index can always be rebuilt:

- Each document row records its `status` (`processing`, `ready` or `failed`) and the `index_version` it was indexed with.
- An upload stores the file first, then indexes it, then marks it `ready`. If anything fails, the chunks, stored file and row are removed. If the server stops mid-way, the row stays `processing`.
- When the backend starts, a background thread (`main.recover_index`) re-indexes, from the stored file, every document whose chunks are missing, that is still `processing`, or that has an older `INDEX_VERSION`. It then removes chunks whose document no longer exists (for example after an account was deleted in Supabase). A file that is missing or unreadable marks the document `failed`. If storage is down, the document is left for the next start.
- A failed document can be retried from the sidebar (`POST /api/documents/{id}/reindex`).
- Nothing is re-indexed during normal requests. While a document is being rebuilt, the sidebar shows **Indexing…** and refreshes until it is ready.

## Data storage

| Where | Contents |
|---|---|
| Supabase PostgreSQL (`DATABASE_URL`) | `profiles` (one per user, linked to `auth.users`), `documents` (metadata, owner, storage path, status, subject, course), `conversations`, `messages` and `quiz_attempts`. Every table has a `user_id` and Row Level Security. Deleting a user in Supabase deletes all their rows |
| Supabase Storage, private bucket `documents` | The uploaded files, at `<user id>/<document id>.<ext>` |
| `skimmi/backend/data/chroma_db/` | ChromaDB: collection `skimmi_documents`, one chunk per record with `{user_id, doc_id, source, page}` metadata. Rebuilt from storage when needed |

The schema is defined in `app/database.py` (SQLAlchemy Core) and created by Alembic migrations in `skimmi/backend/migrations/`. To change it, edit the tables and add a migration (`alembic revision -m "…"`, from `skimmi/backend`). Large files and embeddings are never stored in PostgreSQL.

Each quiz attempt stores the score, the chosen options and the quiz's title and scope; its link to the quiz message is cleared (not deleted) when the chat is deleted. Each message stores its study `mode` and a readable `scope` label (for example `notes.pdf · pages 3–7 · enzymes`). Quiz and flashcard answers are stored as JSON in `content`.

## API

Every route except `/api/health` needs `Authorization: Bearer <Supabase access token>` and only ever sees the signed-in user's data.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Public: `{"status": "ok"}` when the API and its database are up |
| `GET` | `/api/me` | The account: `{id, email, created_at, documents, conversations, quiz_attempts}` |
| `GET` | `/api/documents` | List documents, each with `subject`, `course`, `status` and `error` |
| `POST` | `/api/documents` | Upload a PDF (multipart field `file`, optional field `subject`) |
| `PATCH` | `/api/documents/{id}` | Set `{subject, course}`. An empty subject removes both |
| `DELETE` | `/api/documents/{id}` | Remove a document's chunks, stored file and row |
| `POST` | `/api/documents/{id}/reindex` | Index a document again from its stored file |
| `GET` | `/api/documents/{id}/file` | Download the original file |
| `GET` | `/api/conversations` | List chats, most recently used first |
| `GET` | `/api/conversations/{id}` | A chat with all its messages |
| `DELETE` | `/api/conversations/{id}` | Delete a chat and its messages |
| `POST` | `/api/chat` | Ask a question or run a study mode (body below). Without a `conversation_id`, a new chat is started. Returns `{conversation, question, message}`: the saved user message and the answer, where `message.sources` is `[{doc_id, source, page, excerpt, text}]` (`excerpt` is the best-matching part of the passage, `text` the whole passage) |
| `POST` | `/api/messages/{id}/quiz-attempts` | Score a completed quiz and save the result. Body `{"answers": [1, 0, 3]}`: the chosen option's index per question. Returns the attempt with `correct`, `total` and per-question `results` |
| `GET` | `/api/progress` | `{quizzes_completed, questions_answered, correct_answers, accuracy, activity, recent}` |

`/api/chat` body:

```json
{
  "question": "How does RSA work?",
  "mode": "explain",
  "doc_id": "…",
  "subject": "Biology",
  "page_from": 3,
  "page_to": 7,
  "topic": "public-key encryption",
  "conversation_id": "…"
}
```

Every field except `question` is optional. `mode` is one of `ask` (the default), `explain`, `summarize`, `quiz` and `flashcards`. `question` is required for `ask` and `explain`. For the other modes it may be empty, and the saved message then reads like "Quiz me on notes.pdf". A page range needs a `doc_id`. `subject` is used only without a `doc_id`. For `quiz` and `flashcards`, `message.content` is a JSON string: `{"questions": [{type, question, options, answer, explanation, source, page}], "request": {doc_id, subject, topic, page_from, page_to}}` or `{"cards": [{front, back, source, page}]}`. Quiz messages in `GET /api/conversations/{id}` also have `last_attempt` (`{correct, total, completed_at}` or null).

Errors return `{"detail": "a readable message", "code": "slug"}`: 400 for invalid files, questions, page ranges or quiz answers, 401 for a missing, invalid or expired session, 404 for unknown (or someone else's) documents, subjects, chats or quizzes, 409 for a file that's already uploaded, the document limit, or a document that is still indexing or failed, 410 when a document's stored file is missing, 413 for a file or request that's too large, 422 for a file (or a selected page range) with no text or an invalid request, 429 when a rate limit is reached (with `Retry-After`), and 503 when Gemini, the database, storage or search is unavailable, or Gemini returns a quiz or flashcard set that can't be used. A question and its answer are saved only when the answer succeeds, so a failed question can simply be asked again.

## Tests

```bash
cd skimmi/backend
python -m pytest
```

The tests are offline and isolated: each one gets a fresh SQLite database created by the real Alembic migrations, local file storage and ChromaDB in a temporary folder, a fake embedder, a stubbed Gemini call and test access tokens signed with a test secret for two users. They never touch Supabase or real data.

- `test_auth.py`: every data route rejects missing, expired, forged, wrong-audience, wrong-issuer, anon-role and `alg: none` tokens; asymmetric (ES256) tokens are verified with the project's keys; key-fetch outages give 503; failed tokens are rate limited; profiles are created on first use.
- `test_isolation.py`: a second user can't list, read, change, delete, download, re-index, search, quiz on or continue another user's documents, subjects, chats, quizzes or progress, whatever ids they send; both users can upload the same file; unscoped searches are refused.
- `test_reliability.py`: storage, search, database and AI outages give clean 503s and leave nothing half-saved; upload limits, path tricks, body size limits, rate limits, CORS and error shapes; the vector index is rebuilt from storage after being lost, interrupted uploads are finished, orphaned chunks removed, failed documents re-indexed on demand.
- `test_migrations.py`: the migrations match the models, constraints (duplicates, foreign keys, scores) and cascading deletes. With a disposable PostgreSQL, it also checks Row Level Security: a user reads only their own rows, can't write, and the anon role sees nothing.
- `test_import_legacy.py`: moving the single-user data into an account.
- `test_api.py`, `test_study.py`, `test_learning.py`, `test_rag.py`, `test_formats.py`: the existing features (uploads, chat and conversations, study modes, quizzes and progress, subjects, scope, chunking, excerpts, file formats).

To run the PostgreSQL checks (Docker):

```bash
docker run -d --name skimmi-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
export SKIMMI_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres
python -m pytest tests/test_migrations.py            # RLS and schema on PostgreSQL
SKIMMI_TEST_ALL_ON_POSTGRES=1 python -m pytest       # the whole suite on PostgreSQL
```

These drop and recreate the database's schema, so only point them at a throwaway database.

To try the app by hand, sign up with two accounts (in two browsers or a private window) and upload a different file in each. Check that neither sees the other's documents, chats or progress. Then, in one account:

1. **Explain:** select the document, ask "What is …?", then follow up with "How is that different from …?".
2. **Summarize:** set a page range and press Enter with the box empty. Check that the sources only list pages in that range.
3. **Quiz:** enter a topic and press Enter. Answer every question, getting at least one wrong on purpose, and check the feedback and explanation. On the results, check the score and the review, then **Retry quiz** and **New quiz**. The **Study progress** card should update. Refresh the page: the quiz shows its last score. Ask "Why is question 2's answer correct?" as a follow-up.
4. **Flashcards:** press Enter and flip a few cards.
5. **Subjects:** give two documents a subject with the tag button and leave a third without one. The sidebar should group them, and the chips should filter the list. Pick **All of <subject>** above the message box and ask a question: the sources should only come from that subject's documents.
6. **Sources:** after an answer, click **show excerpts** and check that each passage names the document and page and that **Show the whole passage** expands it.
7. Ask about something that isn't in your PDFs. Skimmi should say it isn't covered.

## Project structure

```
skimmi/
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── migrations/            Alembic migrations (schema, foreign keys, Row Level Security)
│   ├── app/
│   │   ├── config.py          Settings, read from environment variables
│   │   ├── auth.py            Verifies Supabase access tokens; the current_user dependency
│   │   ├── ratelimit.py       In-memory rate limits
│   │   ├── database.py        PostgreSQL/SQLite schema and per-user queries (SQLAlchemy Core)
│   │   ├── storage.py         Private Supabase Storage (or local files) for the original uploads
│   │   ├── vectors.py         The vector store (ChromaDB): the only module that talks to it
│   │   ├── extract.py         Reads each file type into pages of text
│   │   ├── rag.py             The RAG pipeline (chunk, embed, retrieve, generate) and study modes
│   │   ├── progress.py        Quiz scoring and the progress summary
│   │   ├── import_legacy.py   One-off: move single-user data into an account
│   │   └── main.py            FastAPI routes, errors, upload/delete flows, index recovery
│   ├── tests/
│   └── data/                  Created at runtime: ChromaDB (and local files in development)
└── frontend/
    ├── .env.example
    └── src/
        ├── AuthGate.jsx       Shows the sign-in screen or the app; handles password recovery
        ├── supabase.js        Supabase client (auth only) and friendly auth messages
        ├── App.jsx            App state: documents, uploads, chats, study mode and scope, progress
        ├── api.js             Calls to the backend, with the access token and refresh-on-401
        ├── modes.js           The study modes' labels, icons and hints
        ├── subjects.js        Grouping documents by subject and course
        └── components/        AuthScreen, AccountPanel, Sidebar, chat list, messages, Sources,
                               composer, empty states, StudyControls, Study, Progress
```

## Limitations

- Only text-based PDFs are supported (no OCR).
- Summaries, quizzes and flashcards of a long document are built from at most 16 chunks (about 13,000 characters). For a thorough result on a long book, select a page range or a topic.
- Chapters are chosen by page range or a topic. Skimmi doesn't detect a document's chapter structure itself.
- Quizzes are multiple choice only, one quiz at a time. There are no question banks, schedules or adaptive difficulty.
- Excerpts are chosen by word overlap with the question, so the highlighted sentences are a good guide but not always the exact sentence Gemini used.
- There is no minimum relevance: a question the documents don't cover still gets the closest passages (Gemini then says it isn't covered). The embedding model is English-only, so a fixed cutoff would misjudge other languages.
- Rate limits are kept in memory, so they are per backend process and reset on restart. Several instances would need a shared store (Redis).
- Deleting an account in Supabase deletes the user's database rows, and the next startup removes their chunks, but their stored files stay in the bucket until removed by hand. There is no in-app account deletion yet.
- Uploads are indexed during the request (a few seconds per file). Very large files would be better handled by a background worker.
- Deployment (hosting, HTTPS, production CORS and redirect URLs) is not set up yet.
- Gemini's free tier has a small daily request limit per model. Skimmi falls back to the models in `GEMINI_FALLBACK_MODELS` (each has its own quota) and only reports a quota error when all of them are used up.
