"""Сбор тем Discourse из RSS или category API и ответов из topic API."""

import calendar
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import feedparser
from bs4 import BeautifulSoup

from researcher.config import settings
from researcher.models import Source

from .base import Item, client


def _base_url(source: Source) -> str:
    if base_url := source.config.get("base_url"):
        return base_url.rstrip("/")
    feed_url = urlsplit(source.config["feed_url"])
    return f"{feed_url.scheme}://{feed_url.netloc}"


def _text(value: str | None) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def _topic_id(value: str) -> str | None:
    match = re.search(r"/t/(?:[^/]+/)?(\d+)(?:/|$)", urlsplit(value).path)
    return match.group(1) if match else None


def _fetch_feed(source: Source) -> list[Item]:
    with client() as http:
        response = http.get(source.config["feed_url"])
        response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Invalid feed: {source.name}")
    items = []
    seen = set()
    limit = source.config.get("limit", settings.source_item_limit)
    if limit <= 0:
        return items
    for entry in feed.entries:
        guid = entry.get("id", "")
        url = entry.get("link") or (guid if urlsplit(guid).scheme in {"http", "https"} else "")
        title = _text(entry.get("title"))
        external_id = _topic_id(url) or _topic_id(guid) or guid or url
        if not (external_id and url and title) or external_id in seen:
            continue
        date = entry.get("published_parsed")
        published_at = datetime.fromtimestamp(calendar.timegm(date), UTC) if date else None
        body = (entry.get("content") or [{}])[0].get("value") or entry.get("summary", "")
        items.append(Item(external_id, url, title, _text(body), published_at))
        seen.add(external_id)
        if len(items) >= limit:
            break
    return items


def fetch_discourse(source: Source) -> list[Item]:
    """Получить темы из настроенного RSS или прежнего category API."""
    if "feed_url" in source.config:
        return _fetch_feed(source)
    base_url = _base_url(source)
    category = source.config["category"].strip("/")
    limit = source.config.get("limit", settings.source_item_limit)
    lookback = source.config.get("lookback_days", settings.source_lookback_days)
    cutoff = datetime.now(UTC) - timedelta(days=lookback) if lookback else None
    with client() as http:
        response = http.get(f"{base_url}/c/{category}.json")
        response.raise_for_status()
        topics = response.json().get("topic_list", {}).get("topics", [])[:limit]
        items = []
        for topic in topics:
            published_at = datetime.fromisoformat(topic["created_at"])
            if cutoff and published_at < cutoff:
                continue
            # Список категории содержит метаданные, но полный текст первой
            # публикации берём отдельно, чтобы не спутать его с ответами.
            response = http.get(f"{base_url}/raw/{topic['id']}")
            response.raise_for_status()
            items.append(Item(
                external_id=str(topic["id"]),
                url=f"{base_url}/t/{topic.get('slug', topic['id'])}/{topic['id']}",
                title=_text(topic.get("title")),
                text=response.text.strip(),
                published_at=published_at,
                metadata={
                    "views": topic.get("views", 0),
                    "replies": topic.get("reply_count", max(topic.get("posts_count", 1) - 1, 0)),
                },
            ))
    return items


def fetch_discourse_context(source: Source, external_id: str) -> str:
    """Вернуть ограниченное число ответов, пропустив первый пост автора."""
    if not external_id.isdigit():
        return ""
    limit = source.config.get("max_comments", settings.max_comments_per_publication)
    with client() as http:
        response = http.get(f"{_base_url(source)}/t/{external_id}.json")
        response.raise_for_status()
    posts = response.json().get("post_stream", {}).get("posts", [])[1:limit + 1]
    return "\n".join(
        f"[reply by {post.get('username', 'unknown')}] {_text(post.get('cooked') or post.get('raw'))}"
        for post in posts
        if _text(post.get("cooked") or post.get("raw"))
    )
