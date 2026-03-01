"""baseline: books table as-is

Revision ID: c06702449296
Revises: 
Create Date: 2026-02-24 12:03:56.356849

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c06702449296'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Baseline must be safe on shared PostgreSQL servers and idempotent for
    existing librarian deployments. It creates only the `books` table if
    missing, and never drops/rewrites unrelated tables (including pgvector
    `data_*` collections).
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("books"):
        op.create_table(
            "books",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("authors", sa.ARRAY(sa.Text()), nullable=True),
            sa.Column("isbn", sa.String(length=20), nullable=True),
            sa.Column("format", sa.String(length=20), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("status_message", sa.Text(), nullable=True),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("source_hash", sa.String(length=64), nullable=True),
            sa.Column("converted_path", sa.Text(), nullable=True),
            sa.Column("subjects", sa.ARRAY(sa.Text()), nullable=True),
            sa.Column("library", sa.String(length=100), nullable=True),
            sa.Column("extraction_duration_s", sa.Float(), nullable=True),
            sa.Column("metadata", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema.

    Intentionally non-destructive in baseline revision.
    """
    pass
