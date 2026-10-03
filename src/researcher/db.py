"""Общий SQLAlchemy engine и фабрика короткоживущих сессий."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from researcher.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
# Сохраняем доступ к загруженным полям после commit для логирования задач.
SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=Session)
