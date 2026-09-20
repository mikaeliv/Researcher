import logging
from datetime import UTC, datetime

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import func, select

from researcher.config import settings
from researcher.db import SessionLocal
from researcher.models import Cluster, Publication, Source, Stage
from researcher.pipeline import eligible, ingest, process
from researcher.telegram import publish

log = logging.getLogger(__name__)
celery_app = Celery("researcher", broker=settings.redis_url)
celery_app.conf.update(task_serializer="json", accept_content=["json"], timezone="UTC",
                       task_acks_late=True, worker_prefetch_multiplier=1,
                       beat_schedule={
                           "collect": {"task": "researcher.tasks.collect_all", "schedule": crontab(minute=0, hour="*/3")},
                           "process": {"task": "researcher.tasks.process_pending", "schedule": crontab(minute="*/10")},
                           "publish": {"task": "researcher.tasks.publish_pending", "schedule": crontab(minute=30, hour="9,13,18")},
                           "digest": {"task": "researcher.tasks.weekly_digest", "schedule": crontab(minute=0, hour=10, day_of_week="mon")},
                       })


@celery_app.task
def collect_all() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(Source.id).where(Source.enabled.is_(True))).all()
    for source_id in ids:
        collect_source.delay(source_id)


@celery_app.task
def collect_source(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None or not source.enabled:
            return
        # One fetch per source at a time even if scheduled twice.
        locked = db.scalar(select(func.pg_try_advisory_xact_lock(source_id + 1_000_000)))
        if not locked:
            return
        try:
            count = ingest(db, source)
            log.info("Collected %s new items from %s", count, source.name)
        except Exception as exc:
            db.rollback()
            source.last_error = str(exc)[:1000]
            db.commit()
            log.exception("Collection failed: %s", source.name)
            raise
    process_pending.delay()


@celery_app.task
def process_pending() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(Publication.id).where(Publication.stage == Stage.NEW)
                         .order_by(Publication.id).limit(100)).all()
    for pub_id in ids:
        process_one.delay(pub_id)


@celery_app.task
def process_one(pub_id: int) -> None:
    with SessionLocal() as db:
        # Serialize AI analysis + cluster assignment to keep clusters and budget consistent.
        if not db.scalar(select(func.pg_try_advisory_xact_lock(55_000_002))):
            return
        pub = db.scalar(select(Publication).where(Publication.id == pub_id).with_for_update(skip_locked=True))
        if pub is None or pub.stage != Stage.NEW:
            return
        try:
            process(db, pub)
        except RuntimeError as exc:
            db.rollback()
            if "budget reached" in str(exc) or "API_KEY" in str(exc):
                log.warning("AI processing paused: %s", exc)
                return
            raise
        except Exception as exc:
            db.rollback()
            pub = db.get(Publication, pub_id)
            pub.retry_count += 1
            pub.error = str(exc)[:1000]
            if pub.retry_count >= 3:
                pub.stage = Stage.FAILED
            db.commit()
            log.exception("Processing failed: %s", pub_id)


@celery_app.task
def publish_pending() -> None:
    if not settings.telegram_bot_token or not settings.telegram_channel_id:
        return
    with SessionLocal() as db:
        # Post once per global time window. Keep the lock until commit after the HTTP call.
        if not db.scalar(select(func.pg_try_advisory_xact_lock(55_000_001))):
            return
        today = datetime.now(UTC).date()
        count = db.scalar(select(func.count(Cluster.id)).where(func.date(Cluster.published_at) == today)) or 0
        remaining = max(0, settings.max_daily_posts - count)
        clusters = db.scalars(select(Cluster).where(Cluster.published_at.is_(None))
                              .order_by(Cluster.score.desc()).limit(remaining * 4)).all()
        for cluster in clusters:
            if remaining == 0:
                break
            if eligible(db, cluster):
                publish(db, cluster)
                remaining -= 1
        db.commit()


@celery_app.task
def weekly_digest(chat_id: int | str | None = None) -> None:
    # A digest is generated from published clusters; all links remain in their original posts.
    target_chat_id = chat_id or settings.telegram_channel_id
    if not settings.telegram_bot_token or not target_chat_id:
        return
    from datetime import timedelta
    from html import escape

    import httpx

    from researcher.models import utcnow
    with SessionLocal() as db:
        clusters = db.scalars(select(Cluster).where(Cluster.published_at >= utcnow() - timedelta(days=7))
                              .order_by(Cluster.score.desc()).limit(10)).all()
        if not clusters:
            text = "За последние 7 дней опубликованных проблем нет"
        else:
            lines = [f"{i}. {escape(c.title[:120])} — {c.score}/100" for i, c in enumerate(clusters, 1)]
            text = "📌 <b>Лучшие проблемы недели</b>\n\n" + "\n".join(lines)
    with httpx.Client(timeout=30) as http:
        resp = http.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                         json={"chat_id": target_chat_id, "text": text, "parse_mode": "HTML"})
        resp.raise_for_status()
