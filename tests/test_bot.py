import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from researcher import bot


def test_status_distinguishes_collected_and_published(monkeypatch):
    db = MagicMock()
    db.scalar.side_effect = [180, 3, 0, 7]
    db.scalars.return_value.all.return_value = [SimpleNamespace(id=1, name="Hacker News / Ask HN")]
    db.execute.return_value.all.return_value = [
        (1, "filtered", 10),
        (1, "new", 2),
        (1, "analyzed", 4),
        (1, "rejected", 3),
        (1, "failed", 1),
    ]
    session = MagicMock()
    session.__enter__.return_value = db
    message = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    monkeypatch.setattr(bot.settings, "telegram_owner_id", 7)
    monkeypatch.setattr(bot, "SessionLocal", Mock(return_value=session))

    asyncio.run(bot.status(message))

    message.answer.assert_awaited_once_with(
        "Активных источников: 7\n"
        "Собрано записей: 180\n"
        "Кластеров: 3\n"
        "Опубликовано кластеров: 0\n\n"
        "По источникам:\n"
        "Hacker News / Ask HN: raw=20, pre-LLM filtered=10, pending=2, LLM analyzed=7, "
        "accepted=4, LLM rejected=3, failed=1"
    )


def test_digest_targets_calling_chat(monkeypatch):
    task = Mock()
    message = SimpleNamespace(
        text="/digest",
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(bot.settings, "telegram_owner_id", 7)
    monkeypatch.setattr(bot, "weekly_digest", task)

    asyncio.run(bot.run(message))

    task.delay.assert_called_once_with(42)
    message.answer.assert_awaited_once_with("Задача поставлена в очередь")
