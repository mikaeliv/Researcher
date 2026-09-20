from datetime import datetime

from researcher.models import Source

from .base import Item, client


def fetch_appstore(source: Source) -> list[Item]:
    """Public Apple customer review feed; availability and paging vary by country/app."""
    country = source.config.get("country", "us")
    app_id = source.config["app_id"]
    with client() as http:
        resp = http.get(f"https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortby=mostrecent/json")
        resp.raise_for_status()
        data = resp.json()
    entries = data.get("feed", {}).get("entry", [])
    return [Item(str(e["id"]["label"]), e.get("link", {}).get("attributes", {}).get("href", ""),
                 e.get("title", {}).get("label", ""), e.get("content", {}).get("label", ""),
                 datetime.fromisoformat(e["updated"]["label"]))
            for e in entries if isinstance(e, dict) and "im:rating" in e]
