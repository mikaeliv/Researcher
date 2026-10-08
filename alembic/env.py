"""Настройка миграций Alembic для рабочей БД или offline SQL-режима."""

from sqlalchemy import engine_from_config, pool

from alembic import context
from researcher.config import settings
from researcher.models import Base

config = context.config
# Alembic читает URL через ConfigParser, поэтому символ % требуется удвоить.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Сгенерировать SQL без соединения с БД."""
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции в отдельном подключении без пула."""
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
