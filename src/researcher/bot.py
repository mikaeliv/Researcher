import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from researcher.config import settings
from researcher.db import SessionLocal
from researcher.models import Cluster, Feedback, Publication, Source
from researcher.tasks import collect_all, publish_pending, weekly_digest

dp = Dispatcher()


def owner(user_id: int | None) -> bool:
    return user_id is not None and settings.telegram_owner_id is not None and user_id == settings.telegram_owner_id


@dp.message(Command("status", "stats"))
async def status(message: Message) -> None:
    if not owner(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        pubs = db.scalar(select(func.count(Publication.id)))
        clusters = db.scalar(select(func.count(Cluster.id)))
        published = db.scalar(select(func.count(Cluster.id)).where(Cluster.published_at.is_not(None)))
        enabled = db.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True)))
    await message.answer(
        f"Активных источников: {enabled}\n"
        f"Собрано записей: {pubs}\n"
        f"Кластеров: {clusters}\n"
        f"Опубликовано кластеров: {published}"
    )


@dp.message(Command("sources"))
async def sources(message: Message) -> None:
    if not owner(message.from_user.id if message.from_user else None):
        return
    with SessionLocal() as db:
        rows = db.scalars(select(Source).order_by(Source.id)).all()
        result = "\n".join(f"{s.id}: {s.name} ({'on' if s.enabled else 'off'})" for s in rows)
    await message.answer(result[:4000] or "Источников пока нет")


@dp.message(Command("pause", "resume"))
async def toggle(message: Message) -> None:
    if not owner(message.from_user.id if message.from_user else None):
        return
    enabled = message.text.startswith("/resume")
    with SessionLocal() as db:
        for source in db.scalars(select(Source)).all():
            source.enabled = enabled
        db.commit()
    await message.answer("Сбор включён" if enabled else "Сбор остановлен")


@dp.message(Command("collect", "publish", "digest"))
async def run(message: Message) -> None:
    if not owner(message.from_user.id if message.from_user else None):
        return
    if message.text.startswith("/digest"):
        weekly_digest.delay(message.chat.id)
    else:
        task = collect_all if message.text.startswith("/collect") else publish_pending
        task.delay()
    await message.answer("Задача поставлена в очередь")


@dp.callback_query(F.data.startswith("vote:"))
async def vote(callback: CallbackQuery) -> None:
    if not owner(callback.from_user.id):
        await callback.answer("Только владелец может оценивать карточки", show_alert=True)
        return
    try:
        _, cluster_id, rating = callback.data.split(":", 2)
        if rating not in ("up", "down", "star"):
            raise ValueError
        with SessionLocal() as db:
            vote = db.scalar(select(Feedback).where(Feedback.cluster_id == int(cluster_id),
                                                   Feedback.user_id == callback.from_user.id))
            if vote:
                vote.rating = rating
            else:
                db.add(Feedback(cluster_id=int(cluster_id), user_id=callback.from_user.id, rating=rating))
            db.commit()
        await callback.answer("Оценка сохранена")
    except (ValueError, AttributeError):
        await callback.answer("Некорректная оценка", show_alert=True)


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN required")
    session = AiohttpSession(
        proxy="socks5://host.docker.internal:20170"
    )
    bot = Bot(
        token=settings.telegram_bot_token,
        session=session,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
