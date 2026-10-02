"""Alembic environment: migrates the database at config.DATABASE_URL."""

from logging.config import fileConfig

from alembic import context

from app import database

cfg = context.config
if cfg.config_file_name and cfg.attributes.get("configure_logging", True):
    fileConfig(cfg.config_file_name)

target_metadata = database.metadata


def run_migrations_offline():
    context.configure(url=database.database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = cfg.attributes.get("engine") or database.get_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=database.include_in_autogenerate,
            render_as_batch=connection.dialect.name == "sqlite",  # SQLite can't ALTER most things
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
