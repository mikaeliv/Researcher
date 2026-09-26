from collections import deque
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from researcher.config import settings
from researcher.models import Source

from .base import Item, client

API_URL = "https://hacker-news.firebaseio.com/v0"


def _text(value: str | None) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def fetch_hackernews(source: Source) -> list[Item]:
    limit = source.config.get("limit", settings.source_item_limit)
    lookback = source.config.get("lookback_days", settings.source_lookback_days)
    cutoff = datetime.now(UTC) - timedelta(days=lookback) if lookback else None
    items = []
    with client() as http:
        response = http.get(f"{API_URL}/askstories.json")
        response.raise_for_status()
        for item_id in response.json()[:limit]:
            response = http.get(f"{API_URL}/item/{item_id}.json")
            response.raise_for_status()
            data = response.json()
            if not data or data.get("dead") or data.get("deleted") or data.get("type") != "story":
                continue
            published_at = datetime.fromtimestamp(data["time"], UTC)
            if cutoff and published_at < cutoff:
                continue
            items.append(Item(
                external_id=str(data["id"]),
                url=f"https://news.ycombinator.com/item?id={data['id']}",
                title=_text(data.get("title")),
                text=_text(data.get("text")),
                published_at=published_at,
                metadata={"score": data.get("score", 0), "comments": data.get("descendants", 0)},
            ))
    return items


def fetch_hackernews_context(source: Source, external_id: str) -> str:
    limit = source.config.get("max_comments", settings.max_comments_per_publication)
    max_depth = source.config.get("max_comment_depth", settings.max_comment_depth)
    comments = []
    with client() as http:
        response = http.get(f"{API_URL}/item/{external_id}.json")
        response.raise_for_status()
        story = response.json() or {}
        pending = deque((item_id, 0) for item_id in story.get("kids", []))
        while pending and len(comments) < limit:
            item_id, depth = pending.popleft()
            response = http.get(f"{API_URL}/item/{item_id}.json")
            response.raise_for_status()
            data = response.json() or {}
            text = _text(data.get("text"))
            if text and not data.get("dead") and not data.get("deleted"):
                label = "top-level" if depth == 0 else f"reply depth={depth}"
                comments.append(f"[{label}] {text}")
            if depth < max_depth:
                pending.extend((child, depth + 1) for child in data.get("kids", []))
    return "\n".join(comments)
