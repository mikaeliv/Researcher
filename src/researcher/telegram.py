from datetime import UTC, datetime
from html import escape

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from researcher.config import settings
from researcher.models import Cluster, Evidence, Publication, Source


def card(db: Session, cluster: Cluster) -> str:
    rows = db.execute(select(Evidence, Publication, Source).join(Publication, Evidence.publication_id == Publication.id)
                      .join(Source, Publication.source_id == Source.id)
                      .where(Evidence.cluster_id == cluster.id).order_by(Evidence.id.desc()).limit(5)).all()
    sources = "\n".join(f'• <a href="{escape(p.url, quote=True)}">{escape(s.name)}</a>' for _, p, s in rows if p.url)
    workarounds = next((e.workaround for e, _, _ in rows if e.workaround), None)
    return (f"💡 <b>{escape(cluster.title[:150])}</b>\n\n"
            f"{escape(cluster.description[:1000])}\n\n"
            f"Кому: {escape(cluster.audience[:200] or 'не определено')}\n"
            f"Как решают сейчас: {escape((workarounds or 'не указано')[:300])}\n"
            f"Оценка сигнала: {cluster.score}/100 · Свидетельств: {len(rows)}+\n\n"
            f"Источники:\n{sources}")[:3900]


def publish(db: Session, cluster: Cluster) -> None:
    if not settings.telegram_bot_token or not settings.telegram_channel_id:
        raise RuntimeError("Telegram bot token and channel ID required")
    message = {"chat_id": settings.telegram_channel_id, "text": card(db, cluster),
               "parse_mode": "HTML", "disable_web_page_preview": True,
               "reply_markup": {"inline_keyboard": [[
                   {"text": "👍 Полезно", "callback_data": f"vote:{cluster.id}:up"},
                   {"text": "👎 Шум", "callback_data": f"vote:{cluster.id}:down"},
                   {"text": "🔥 Изучить", "callback_data": f"vote:{cluster.id}:star"}]]}}
    with httpx.Client(timeout=30) as http:
        resp = http.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage", json=message)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description", "Telegram failure")))
    cluster.telegram_message_id = data["result"]["message_id"]
    cluster.published_at = datetime.now(UTC)
    db.flush()
