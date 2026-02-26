"""Database models and session management.

Provides the books table as the source of truth for the librarian pipeline,
replacing Calibre's SQLite for ID assignment and pipeline state tracking.

Uses the same PostgreSQL instance as pgvector (configured via LIBRARIAN_DB_URL).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Column,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from librarian.config import load_config


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)
    authors = Column(ARRAY(Text), default=list)
    isbn = Column(String(20))
    format = Column(String(20))  # pdf, epub, kindle
    status = Column(String(20), nullable=False, default="pending")
    # pending -> extracting -> extracted -> indexing -> indexed | failed
    status_message = Column(Text)  # human-readable progress message
    source_path = Column(Text)
    source_hash = Column(String(64))
    converted_path = Column(Text)
    subjects = Column(ARRAY(Text), default=list)
    library = Column(String(100))
    extraction_duration_s = Column(Float)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<Book(id={self.id}, title='{self.title[:40]}', status='{self.status}')>"


_engine = None
_session_factory = None


def get_engine(config: dict | None = None):
    """Get the singleton SQLAlchemy engine.

    Creates the engine on first call with a bounded connection pool.
    Subsequent calls return the same engine regardless of config arg.
    """
    global _engine
    if _engine is None:
        if config is None:
            config = load_config()
        url = config["vector_store"]["pgvector_url"]
        sa_url = url.replace("postgresql://", "postgresql+psycopg2://")
        _engine = create_engine(sa_url, pool_size=10, max_overflow=5, pool_pre_ping=True)
    return _engine


def get_session(config: dict | None = None) -> Session:
    """Create a new database session from the shared engine."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine(config)
        _session_factory = sessionmaker(bind=engine)
    return _session_factory()


def init_db(config: dict | None = None):
    """Create all tables if they don't exist.

    Safe to call multiple times — only creates missing tables.
    """
    engine = get_engine(config)
    Base.metadata.create_all(engine)

    # Set the sequence to start after the highest existing ID
    # so new inserts don't collide with backfilled Calibre IDs
    with engine.connect() as conn:
        result = conn.execute(text("SELECT MAX(id) FROM books"))
        max_id = result.scalar()
        if max_id:
            conn.execute(text(f"SELECT setval('books_id_seq', {max_id})"))
            conn.commit()
