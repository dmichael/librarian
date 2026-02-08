"""Database models and session management.

Provides the books table as the source of truth for the librarian pipeline,
replacing Calibre's SQLite for ID assignment and pipeline state tracking.

Uses the same PostgreSQL instance as pgvector (agents.local:5432/librarian).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Column,
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
    # pending -> extracted -> indexed | failed
    source_path = Column(Text)
    source_hash = Column(String(64))
    converted_path = Column(Text)
    subjects = Column(ARRAY(Text), default=list)
    library = Column(String(100))
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Book(id={self.id}, title='{self.title[:40]}', status='{self.status}')>"


def get_engine(config: dict | None = None):
    """Create SQLAlchemy engine from config."""
    if config is None:
        config = load_config()
    url = config["vector_store"]["pgvector_url"]
    # SQLAlchemy needs the +psycopg2 driver suffix
    sa_url = url.replace("postgresql://", "postgresql+psycopg2://")
    return create_engine(sa_url)


def get_session(config: dict | None = None) -> Session:
    """Create a new database session."""
    engine = get_engine(config)
    return sessionmaker(bind=engine)()


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
