"""Initial schema and vector extension.

Revision ID: 0001
Revises:
"""
from alembic import op

from researcher.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Включить pgvector и создать таблицы текущих ORM-моделей."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    """Удалить таблицы приложения, сохранив расширение pgvector."""
    Base.metadata.drop_all(op.get_bind())
