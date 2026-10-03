"""Экспериментальный сбор отзывов из публичного App Store RSS feed."""

from datetime import datetime

from researcher.models import Source

from .base import Item, client


def fetch_appstore(source: Source) -> list[Item]:
    """Прочитать отзывы; доступность и пагинация зависят от страны и приложения."""
    country = source.config.get("country", "us")
    app_id = source.config["app_id"]
    with client() as http:
        resp = http.get(f"https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortby=mostrecent/json")
        resp.raise_for_status()
        data = resp.json()
    entries = data.get("feed", {}).get("entry", [])
    # В feed бывают записи самого приложения; признак рейтинга оставляет
    # только пользовательские отзывы.
    return [Item(str(e["id"]["label"]), e.get("link", {}).get("attributes", {}).get("href", ""),
                 e.get("title", {}).get("label", ""), e.get("content", {}).get("label", ""),
                 datetime.fromisoformat(e["updated"]["label"]))
            for e in entries if isinstance(e, dict) and "im:rating" in e]
