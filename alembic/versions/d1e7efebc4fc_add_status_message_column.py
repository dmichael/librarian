"""add status_message column

Revision ID: d1e7efebc4fc
Revises: c06702449296
Create Date: 2026-02-24 15:58:10.134556

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e7efebc4fc'
down_revision: Union[str, Sequence[str], None] = 'c06702449296'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Idempotent safety migration:
    - If `books` doesn't exist yet, create it with status_message included.
    - If `books` exists but column is missing, add only that column.
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
        return

    columns = {c["name"] for c in inspector.get_columns("books")}
    if "status_message" not in columns:
        op.add_column("books", sa.Column("status_message", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema (column drop only if present)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("books"):
        columns = {c["name"] for c in inspector.get_columns("books")}
        if "status_message" in columns:
            op.drop_column("books", "status_message")
