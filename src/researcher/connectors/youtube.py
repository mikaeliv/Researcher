from datetime import datetime

from researcher.config import settings
from researcher.models import Source

from .base import Item, client


def fetch_youtube(source: Source) -> list[Item]:
    if not settings.youtube_api_key:
        raise ValueError("YouTube API key missing")
    video_id = source.config["video_id"]
    with client() as http:
        resp = http.get("https://www.googleapis.com/youtube/v3/commentThreads", params={
            "key": settings.youtube_api_key, "videoId": video_id, "part": "snippet",
            "maxResults": 100, "order": "time", "textFormat": "plainText"})
        resp.raise_for_status()
    return [Item(c["id"], f"https://www.youtube.com/watch?v={video_id}&lc={c['id']}",
                 source.name, c["snippet"]["topLevelComment"]["snippet"]["textDisplay"],
                 datetime.fromisoformat(c["snippet"]["topLevelComment"]["snippet"]["publishedAt"]))
            for c in resp.json().get("items", [])]
