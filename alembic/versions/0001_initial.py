"""Initial schema and vector extension.

Revision ID: 0001
Revises:
"""
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text

from alembic import op
from researcher.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def initial_metadata() -> MetaData:
    """Keep revision 0001 unchanged when later ORM tables/Source fields are added."""
    metadata = MetaData()
    Table(
        "sources", metadata,
        Column("id", Integer, primary_key=True),
        Column("kind", String(32), nullable=False),
        Column("name", String(180), nullable=False, unique=True),
        Column("config", JSON, nullable=False),
        Column("cursor", Text),
        Column("enabled", Boolean, nullable=False),
        Column("last_success_at", DateTime(timezone=True)),
        Column("last_error", Text),
    )
    for name in ("publications", "clusters", "evidence", "ai_usage", "feedback"):
        Base.metadata.tables[name].to_metadata(metadata)
    return metadata


def upgrade() -> None:
    """Включить pgvector и создать исходную схему приложения."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    initial_metadata().create_all(op.get_bind())


def downgrade() -> None:
    """Удалить исходные таблицы приложения, сохранив расширение pgvector."""
    initial_metadata().drop_all(op.get_bind())
