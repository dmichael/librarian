from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context

from librarian.db import Base, get_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# pgvector tables are managed by llama-index, not alembic
EXCLUDE_TABLES = {"data_librarian_full", "data_librarian_equations", "data_librarian_chapters"}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in EXCLUDE_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout."""
    from librarian.config import load_config

    lib_config = load_config()
    url = lib_config["vector_store"]["pgvector_url"]
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
