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


def book_to_dict(book: "Book") -> dict:
    """Flatten a Book row into a plain metadata dict.

    Exposes the common columns plus publisher/year pulled from the JSONB
    metadata blob, so callers don't need a live session to read fields.
    """
    meta = dict(book.metadata_ or {})
    return {
        "id": book.id,
        "title": book.title or "",
        "authors": list(book.authors or []),
        "isbn": book.isbn or meta.get("isbn"),
        "publisher": meta.get("publisher"),
        "year": meta.get("year"),
        "subjects": list(book.subjects or []),
        "status": book.status,
        "format": book.format,
        "source_path": book.source_path,
        "converted_path": book.converted_path,
    }


def get_book_metadata(
    book_ids: list[int] | None = None, config: dict | None = None
) -> dict[int, dict]:
    """Fetch book metadata from the books table, keyed by id.

    Replaces the old calibredb lookups. Pass book_ids to restrict, or None
    for the whole library.
    """
    session = get_session(config)
    try:
        query = session.query(Book)
        if book_ids is not None:
            query = query.filter(Book.id.in_(list(book_ids)))
        return {book.id: book_to_dict(book) for book in query.all()}
    finally:
        session.close()


# Non-column metadata keys that callers may stash in the JSONB blob. Anything
# outside (columns ∪ this set) is rejected so a typo can't silently no-op.
_JSONB_META_KEYS = {"publisher", "year"}


def update_book_fields(book_id: int, config: dict | None = None, **fields) -> bool:
    """Update a single book; returns False if the book is missing.

    Known columns are set directly; keys in _JSONB_META_KEYS are merged into the
    JSONB metadata blob. An unrecognized key raises ValueError rather than
    silently writing to the blob (catching typos / stale field names).
    """
    column_names = {c.name for c in Book.__table__.columns}
    unknown = set(fields) - column_names - _JSONB_META_KEYS
    if unknown:
        raise ValueError(f"unknown book field(s): {sorted(unknown)}")

    session = get_session(config)
    try:
        book = session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            return False
        meta = dict(book.metadata_ or {})
        for key, value in fields.items():
            if key in column_names:
                setattr(book, key, value)
            else:
                meta[key] = value
        book.metadata_ = meta
        session.commit()
        return True
    finally:
        session.close()


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
