from dataclasses import dataclass
from datetime import datetime

import httpx

from researcher.models import Source


@dataclass(frozen=True)
class Item:
    external_id: str
    url: str
    title: str
    text: str
    published_at: datetime | None = None


def client() -> httpx.Client:
    return httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "researcher/0.1"})


def fetch(source: Source) -> list[Item]:
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
