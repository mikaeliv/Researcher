from datetime import UTC, datetime

from researcher.config import settings
from researcher.models import Source

from .base import Item, client


def fetch_reddit(source: Source) -> list[Item]:
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise ValueError("Reddit API credentials missing")
    headers = {"User-Agent": settings.reddit_user_agent}
    with client() as http:
        auth = http.post("https://www.reddit.com/api/v1/access_token", auth=(settings.reddit_client_id, settings.reddit_client_secret),
                         data={"grant_type": "client_credentials"}, headers=headers)
        auth.raise_for_status()
        token = auth.json()["access_token"]
        resp = http.get(f"https://oauth.reddit.com/r/{source.config['subreddit']}/new",
                        headers={**headers, "Authorization": f"bearer {token}"}, params={"limit": 100})
        resp.raise_for_status()
    return [Item(p["data"]["name"], "https://www.reddit.com" + p["data"]["permalink"],
                 p["data"]["title"], p["data"].get("selftext", ""),
                 datetime.fromtimestamp(p["data"]["created_utc"], UTC))
            for p in resp.json()["data"]["children"] if p["data"].get("selftext")]

