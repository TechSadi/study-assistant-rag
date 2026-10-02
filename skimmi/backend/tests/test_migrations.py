"""The Alembic migrations create exactly the schema the code expects.

The PostgreSQL test (Row Level Security, constraints) runs only when
SKIMMI_TEST_POSTGRES_URL points at a disposable PostgreSQL database, e.g.

    docker run -d --name skimmi-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
    SKIMMI_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres python -m pytest tests/test_migrations.py

It drops and recreates the schema, so never point it at a real database.
"""

import os
import warnings

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import config, database

from .conftest import USER_A, USER_B


def test_migrations_match_the_models(env):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # SQLite can't reflect the lower(filename) index
        with database.get_engine().connect() as conn:
            context = MigrationContext.configure(conn, opts={"include_object": database.include_in_autogenerate})
            assert compare_metadata(context, database.metadata) == []


def test_migrating_twice_is_harmless(env):
    database.migrate()
    with database.get_engine().connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0001"


def test_constraints(env):
    stamp = database.now()
    database.ensure_profile(USER_A)
    doc = {"user_id": USER_A, "filename": "a.pdf", "sha256": "h", "extension": ".pdf", "subject": ""}
    database.create_document({**doc, "id": database.new_id(), "storage_path": "p1"})
    with pytest.raises(database.DuplicateDocument):  # same file twice
        database.create_document({**doc, "id": database.new_id(), "storage_path": "p2", "filename": "b.pdf"})
    with pytest.raises(database.DuplicateDocument):  # same name, different case
        database.create_document({**doc, "id": database.new_id(), "storage_path": "p3", "sha256": "h2",
                                  "filename": "A.PDF"})
    with pytest.raises(database.DuplicateDocument):  # no profile: foreign key
        database.create_document({**doc, "id": database.new_id(), "user_id": USER_B, "storage_path": "p4"})
    with pytest.raises(sa.exc.IntegrityError), database.begin() as conn:
        conn.execute(database.quiz_attempts.insert().values(
            user_id=USER_A, title="t", correct=5, total=2, answers=[], completed_at=stamp))


def test_deleting_a_profile_deletes_all_its_rows(env):
    database.ensure_profile(USER_A)
    cid = database.create_conversation(USER_A, "t")["id"]
    database.add_exchange(USER_A, cid, "q", "a", [])
    with database.begin() as conn:
        conn.execute(database.profiles.delete())
        for table in (database.conversations, database.messages):
            assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 0


# --- PostgreSQL (optional) -----------------------------------------------------------

POSTGRES_URL = os.getenv("SKIMMI_TEST_POSTGRES_URL")

# What Supabase provides and a plain PostgreSQL lacks: the auth schema, auth.uid()
# (read from the request's JWT claims) and the anon/authenticated roles.
SUPABASE_SHIM = """
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA public, auth TO anon, authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
"""


@pytest.fixture
def postgres(monkeypatch):
    if not POSTGRES_URL:
        pytest.skip("SKIMMI_TEST_POSTGRES_URL is not set")
    monkeypatch.setattr(config, "DATABASE_URL", POSTGRES_URL)
    database.get_engine.cache_clear()
    engine = database.get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE; DROP SCHEMA IF EXISTS auth CASCADE; "
                             "CREATE SCHEMA public;"))
        conn.exec_driver_sql(SUPABASE_SHIM)
        conn.execute(sa.text("INSERT INTO auth.users VALUES (:a), (:b)"), {"a": USER_A, "b": USER_B})
    database.migrate()
    yield engine
    engine.dispose()
    database.get_engine.cache_clear()


def _as_user(conn, user_id):
    conn.execute(sa.text("SET LOCAL ROLE authenticated"))
    conn.execute(sa.text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": f'{{"sub": "{user_id}"}}'})


def test_postgres_schema_and_row_level_security(postgres):
    with postgres.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": database.include_in_autogenerate})
        assert compare_metadata(context, database.metadata) == []

    for user in (USER_A, USER_B):
        database.ensure_profile(user)
        cid = database.create_conversation(user, f"chat of {user[:4]}")["id"]
        database.add_exchange(user, cid, "q", "a", [{"page": 1}])

    tables = ("profiles", "conversations", "messages", "documents", "quiz_attempts")
    with postgres.connect() as conn:
        rls = dict(conn.execute(sa.text(
            "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(:t)"), {"t": [*tables, "alembic_version"]}).all())
        assert all(rls.values()) and len(rls) == 6

    # A signed-in user reading through the Data API sees only their own rows...
    with postgres.connect() as conn, conn.begin():
        _as_user(conn, USER_A)
        assert conn.execute(sa.text("SELECT title FROM conversations")).scalars().all() == ["chat of 1111"]
        assert conn.execute(sa.text("SELECT count(*) FROM messages")).scalar() == 2
        assert conn.execute(sa.text("SELECT id::text FROM profiles")).scalars().all() == [USER_A]

    # ...and cannot write at all (writes only go through the backend).
    for statement in ("INSERT INTO conversations VALUES (gen_random_uuid(), :u, 'x', now(), now())",
                      "UPDATE conversations SET title = 'hacked'",
                      "DELETE FROM messages"):
        with postgres.connect() as conn, conn.begin():
            _as_user(conn, USER_A)
            with pytest.raises(sa.exc.ProgrammingError):
                conn.execute(sa.text(statement), {"u": USER_A})

    # The public anon key sees nothing.
    with postgres.connect() as conn, conn.begin():
        conn.execute(sa.text("SET LOCAL ROLE anon"))
        for table in tables:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() == 0

    # Deleting the Supabase account deletes everything the user had.
    with postgres.begin() as conn:
        conn.execute(sa.text("DELETE FROM auth.users WHERE id = :a"), {"a": USER_A})
        assert conn.execute(sa.text("SELECT count(*) FROM conversations")).scalar() == 1


def test_postgres_rejects_a_malformed_id_without_querying(postgres):
    database.ensure_profile(USER_A)
    assert database.get_document(USER_A, "nope") is None
    assert database.get_conversation(USER_A, "1 OR 1=1") is None
