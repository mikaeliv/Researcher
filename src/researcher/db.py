from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from researcher.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=Session)

