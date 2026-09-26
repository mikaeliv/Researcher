from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from researcher.config import settings
from researcher.models import Source

from .base import Item, client


def _base_url(source: Source) -> str:
    return source.config["base_url"].rstrip("/")


def _text(value: str | None) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def fetch_discourse(source: Source) -> list[Item]:
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
