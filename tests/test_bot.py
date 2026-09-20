import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from researcher import bot


def test_status_distinguishes_collected_and_published(monkeypatch):
    db = MagicMock()
    db.scalar.side_effect = [180, 3, 0, 7]
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
        "Опубликовано кластеров: 0"
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
