"""Общий формат собранного материала и выбор коннектора по типу Source."""

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from researcher.models import Source


@dataclass(frozen=True)
class Item:
    """Публикация до записи в БД; external_id уникален в пределах Source."""
    external_id: str
    url: str
    title: str
    text: str
    published_at: datetime | None = None
    metadata: dict[str, str | int] = field(default_factory=dict)


def client() -> httpx.Client:
    """Создать HTTP-клиент с едиными timeout, редиректами и User-Agent."""
    return httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "researcher/0.1"})


def fetch(source: Source) -> list[Item]:
    """Вызвать сборщик для заданного типа источника."""
    # Импорты внутри веток не загружают необязательные зависимости каждого
    # источника при работе с другими площадками.
    if source.kind == "hackernews":
        from .hackernews import fetch_hackernews
        return fetch_hackernews(source)
    if source.kind == "discourse":
        from .discourse import fetch_discourse
        return fetch_discourse(source)
    if source.kind == "lemmy":
        from .lemmy import fetch_lemmy
        return fetch_lemmy(source)
    if source.kind == "rss":
        from .rss import fetch_rss
        return fetch_rss(source)
    if source.kind == "stackexchange":
        from .stackexchange import fetch_stackexchange
        return fetch_stackexchange(source)
    if source.kind == "reddit":
        from .reddit import fetch_reddit
        return fetch_reddit(source)
    if source.kind == "youtube":
        from .youtube import fetch_youtube
        return fetch_youtube(source)
    if source.kind == "appstore":
        from .appstore import fetch_appstore
        return fetch_appstore(source)
    raise ValueError(f"Unknown source kind: {source.kind}")


def fetch_context(source: Source, external_id: str) -> str:
    """Загрузить ответы к публикации, если площадка это поддерживает."""
    # Контекст вызывается после предварительного фильтра и не создаёт
    # отдельные Publication: исходная проблема остаётся проблемой автора.
    if source.kind == "hackernews":
        from .hackernews import fetch_hackernews_context
        return fetch_hackernews_context(source, external_id)
    if source.kind == "discourse":
        from .discourse import fetch_discourse_context
        return fetch_discourse_context(source, external_id)
    if source.kind == "lemmy":
        from .lemmy import fetch_lemmy_context
        return fetch_lemmy_context(source, external_id)
    return ""
