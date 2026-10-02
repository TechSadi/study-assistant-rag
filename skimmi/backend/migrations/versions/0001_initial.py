"""Multi-user schema: profiles, documents, conversations, messages, quiz attempts.

On Supabase PostgreSQL this also:
* links profiles.id to auth.users(id), so deleting an account deletes its rows;
* enables Row Level Security on every table.

Why RLS matters here even though the backend does its own ownership checks: the
frontend holds the Supabase anon key, and Supabase exposes tables in the public
schema through its Data API. Without RLS anyone with that key could read every
table. The policies only let a signed-in user SELECT their own rows; there are
no INSERT/UPDATE/DELETE policies, so all writes go through the backend, which
validates them. The backend connects as the database owner, which bypasses RLS,
and filters every query by the user id from the verified token (database.py).

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

Id = sa.Uuid(as_uuid=False)
BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
Json = sa.JSON().with_variant(JSONB(), "postgresql")
Timestamp = sa.DateTime(timezone=True)

USER_TABLES = ("documents", "conversations", "messages", "quiz_attempts")


def _owner(table):
    return sa.Column(
        "user_id", Id,
        sa.ForeignKey("profiles.id", ondelete="CASCADE", name=f"fk_{table}_user_id_profiles"),
        nullable=False,
    )


def upgrade():
    op.create_table(
        "profiles",
        sa.Column("id", Id, nullable=False),
        sa.Column("email", sa.String(320), nullable=False, server_default=""),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("last_seen_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_profiles"),
    )

    op.create_table(
        "documents",
        sa.Column("id", Id, nullable=False),
        _owner("documents"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(200), nullable=False),
        sa.Column("pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("subject", sa.String(60), nullable=False, server_default=""),
        sa.Column("course", sa.String(60), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="processing"),
        sa.Column("index_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String(300), nullable=False, server_default=""),
        sa.Column("uploaded_at", Timestamp, nullable=False),
        sa.Column("updated_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.CheckConstraint("status IN ('processing', 'ready', 'failed')", name="ck_documents_status"),
        sa.UniqueConstraint("user_id", "sha256", name="uq_documents_user_id_sha256"),
        sa.UniqueConstraint("storage_path", name="uq_documents_storage_path"),
    )
    op.create_index("ix_documents_user_id_uploaded_at", "documents", ["user_id", "uploaded_at"])
    op.create_index(
        "uq_documents_user_id_lower_filename", "documents",
        ["user_id", sa.text("lower(filename)")], unique=True,
    )

    op.create_table(
        "conversations",
        sa.Column("id", Id, nullable=False),
        _owner("conversations"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("updated_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_user_id_updated_at", "conversations", ["user_id", "updated_at"])

    op.create_table(
        "messages",
        sa.Column("id", BigId, nullable=False, autoincrement=True),
        sa.Column(
            "conversation_id", Id,
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_messages_conversation_id_conversations"),
            nullable=False,
        ),
        _owner("messages"),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("sources", Json, nullable=False),
        sa.Column("mode", sa.String(12), nullable=False, server_default="ask"),
        sa.Column("scope", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])
    op.create_index("ix_messages_user_id_role_mode", "messages", ["user_id", "role", "mode"])

    op.create_table(
        "quiz_attempts",
        sa.Column("id", BigId, nullable=False, autoincrement=True),
        _owner("quiz_attempts"),
        sa.Column(
            "message_id", BigId,
            sa.ForeignKey("messages.id", ondelete="SET NULL", name="fk_quiz_attempts_message_id_messages"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("scope", sa.String(300), nullable=False, server_default=""),
        sa.Column("correct", sa.Integer, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("answers", Json, nullable=False),
        sa.Column("completed_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_attempts"),
        sa.CheckConstraint("correct >= 0 AND correct <= total", name="ck_quiz_attempts_score"),
    )
    op.create_index("ix_quiz_attempts_user_id_id", "quiz_attempts", ["user_id", "id"])
    op.create_index("ix_quiz_attempts_message_id", "quiz_attempts", ["message_id"])

    if op.get_bind().dialect.name == "postgresql":
        _secure_postgres()


def _exists(sql):
    return op.get_bind().execute(sa.text(sql)).scalar() is not None


def _secure_postgres():
    # Supabase projects have auth.users and auth.uid(). A plain PostgreSQL (for
    # example a local test database) may not, so each step checks first.
    if _exists("SELECT to_regclass('auth.users')"):
        op.execute(
            "ALTER TABLE profiles ADD CONSTRAINT fk_profiles_id_users "
            "FOREIGN KEY (id) REFERENCES auth.users (id) ON DELETE CASCADE"
        )

    has_uid = _exists("SELECT to_regprocedure('auth.uid()')")
    client_roles = [
        role for role in ("anon", "authenticated")
        if _exists(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'")
    ]
    for table in ("profiles", *USER_TABLES, "alembic_version"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for role in client_roles:
            # Writes only through the backend; reads only through the policy below.
            op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM {role}")
    if not (has_uid and "authenticated" in client_roles):
        return
    op.execute(
        "CREATE POLICY profiles_select_own ON profiles FOR SELECT TO authenticated "
        "USING (id = (SELECT auth.uid()))"
    )
    for table in USER_TABLES:
        op.execute(
            f"CREATE POLICY {table}_select_own ON {table} FOR SELECT TO authenticated "
            f"USING (user_id = (SELECT auth.uid()))"
        )


def downgrade():
    for table in ("quiz_attempts", "messages", "conversations", "documents", "profiles"):
        op.drop_table(table)
