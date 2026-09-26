from datetime import UTC, datetime, timedelta

from researcher.config import settings
from researcher.models import Source

from .base import Item, client


def _base_url(source: Source) -> str:
    return source.config["base_url"].rstrip("/")


def fetch_lemmy(source: Source) -> list[Item]:
    base_url = _base_url(source)
    community = source.config["community"]
    limit = source.config.get("limit", settings.source_item_limit)
    lookback = source.config.get("lookback_days", settings.source_lookback_days)
    cutoff = datetime.now(UTC) - timedelta(days=lookback) if lookback else None
    with client() as http:
        response = http.get(
            f"{base_url}/api/v3/post/list",
            params={"community_name": community, "sort": "New", "limit": limit},
        )
        response.raise_for_status()
    items = []
    for view in response.json().get("posts", []):
        post = view["post"]
        published_at = datetime.fromisoformat(post["published"])
        if cutoff and published_at < cutoff:
            continue
        counts = view.get("counts", {})
        items.append(Item(
            external_id=str(post["id"]),
            url=post.get("ap_id") or f"{base_url}/post/{post['id']}",
            title=post.get("name", ""),
            text=post.get("body", ""),
            published_at=published_at,
            metadata={
                "community": view.get("community", {}).get("name", community),
                "score": counts.get("score", 0),
                "comments": counts.get("comments", 0),
            },
        ))
    return items


def fetch_lemmy_context(source: Source, external_id: str) -> str:
    limit = source.config.get("max_comments", settings.max_comments_per_publication)
    with client() as http:
        response = http.get(
            f"{_base_url(source)}/api/v3/comment/list",
            params={"post_id": external_id, "sort": "Top", "limit": limit},
        )
        response.raise_for_status()
    return "\n".join(
        f"[comment] {view['comment']['content'].strip()}"
        for view in response.json().get("comments", [])[:limit]
        if view.get("comment", {}).get("content", "").strip()
    )
