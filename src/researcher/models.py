"""ORM-модели: источники, публикации, извлечённые свидетельства и кластеры."""

from datetime import UTC, datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from researcher.source_groups import source_group_default


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Stage(StrEnum):
    """Состояния обработки: filtered до AI, rejected после AI, failed после retries."""
    NEW = "new"
    FILTERED = "filtered"
    ANALYZED = "analyzed"
    REJECTED = "rejected"
    FAILED = "failed"


class Source(Base):
    """Настройки одного канала сбора и состояние последнего успешного опроса."""
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(180), unique=True)
    source_group_key: Mapped[str] = mapped_column(String(180), default=source_group_default)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    cursor: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Publication(Base):
    """Сырой материал; внешний ID уникален только внутри своего источника."""
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("source_id", "external_id"), Index("ix_publications_stage", "stage"))
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stage: Mapped[str] = mapped_column(String(20), default=Stage.NEW)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[Source] = relationship()


class Cluster(Base):
    """Общая проблема, объединяющая несколько подтверждённых свидетельств."""
    __tablename__ = "clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    score: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)


class Evidence(Base):
    """Извлечённое свидетельство; одной публикации соответствует не более одного."""
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("publication_id"), Index("ix_evidence_cluster", "cluster_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"))
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"))
    problem: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text, default="")
    workaround: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(Text)
    loss: Mapped[str | None] = mapped_column(Text)
    willingness_to_pay: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    model_name: Mapped[str] = mapped_column(String(100))
    publication: Mapped[Publication] = relationship()
    cluster: Mapped[Cluster | None] = relationship()


class AiUsage(Base):
    """Оценка стоимости вызова AI для контроля месячного бюджета."""
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_usd: Mapped[float] = mapped_column(Float, default=0)


class Feedback(Base):
    """Последняя оценка карточки одним пользователем Telegram."""
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("cluster_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"))
    user_id: Mapped[int] = mapped_column(Integer)
    rating: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Opportunity(Base):
    """Общий product pain/JTBD; пока не участвует в production pipeline."""
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    underlying_pain: Mapped[str] = mapped_column(Text)
    job_to_be_done: Mapped[str] = mapped_column(Text)
    desired_outcome: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    exclusions: Mapped[str] = mapped_column(Text)
    representative_cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
    )
    representative_cluster: Mapped[Cluster] = relationship()
    clusters: Mapped[list["OpportunityCluster"]] = relationship(back_populates="opportunity")


class OpportunityCluster(Base):
    """Один strict Cluster может принадлежать максимум одной Opportunity."""
    __tablename__ = "opportunity_clusters"
    __table_args__ = (UniqueConstraint("cluster_id", name="uq_opportunity_clusters_cluster"),)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity: Mapped[Opportunity] = relationship(back_populates="clusters")
    cluster: Mapped[Cluster] = relationship()


class ClusterOpportunityProfile(Base):
    """Абстракция конкретного Cluster для поиска Opportunity-кандидатов."""
    __tablename__ = "cluster_opportunity_profiles"
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), primary_key=True)
    underlying_pain: Mapped[str] = mapped_column(Text)
    job_to_be_done: Mapped[str] = mapped_column(Text)
    desired_outcome: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    model_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cluster: Mapped[Cluster] = relationship()
