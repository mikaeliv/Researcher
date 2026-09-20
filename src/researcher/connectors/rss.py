import calendar
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from researcher.models import Source

from .base import Item, client


def fetch_rss(source: Source) -> list[Item]:
    with client() as http:
        response = http.get(source.config["url"])
        response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Invalid feed: {source.name}")
    items = []
    for entry in feed.entries[: source.config.get("limit", 50)]:
        date = getattr(entry, "published_parsed", None)
        published = datetime.fromtimestamp(calendar.timegm(date), UTC) if date else None
        body = entry.get("content", [{}])[0].get("value") or entry.get("summary", "")
        items.append(Item(str(entry.get("id") or entry.get("link")), entry.get("link", ""),
                          entry.get("title", ""), BeautifulSoup(body, "html.parser").get_text(" ", strip=True), published))
    return items
