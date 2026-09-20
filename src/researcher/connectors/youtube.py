from datetime import datetime

from researcher.config import settings
from researcher.models import Source

from .base import Item, client

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def fetch_youtube(source: Source) -> list[Item]:
    if not settings.youtube_api_key:
        raise ValueError("YouTube API key missing")
    videos = []
    with client() as http:
        if video_id := source.config.get("video_id"):
            videos.append((video_id, source.name))
        else:
            handles = source.config.get("channel_handles", [])
            if not handles:
                raise ValueError("YouTube source needs video_id or channel_handles")
            video_limit = min(max(int(source.config.get("videos_per_channel", 5)), 1), 10)
            for handle in handles:
                resp = http.get(f"{YOUTUBE_API}/channels", params={
                    "key": settings.youtube_api_key, "forHandle": handle,
                    "part": "snippet,contentDetails"})
                resp.raise_for_status()
                channels = resp.json().get("items", [])
                if not channels:
                    raise ValueError(f"YouTube channel not found: {handle}")
                channel = channels[0]
                resp = http.get(f"{YOUTUBE_API}/playlistItems", params={
                    "key": settings.youtube_api_key,
                    "playlistId": channel["contentDetails"]["relatedPlaylists"]["uploads"],
                    "part": "snippet,contentDetails", "maxResults": video_limit})
                resp.raise_for_status()
                for video in resp.json().get("items", []):
                    if video_id := video.get("contentDetails", {}).get("videoId"):
                        title = f"{channel['snippet']['title']}: {video['snippet']['title']}"
                        videos.append((video_id, title))

        comment_limit = min(max(int(source.config.get("comments_per_order", 25)), 1), 100)
        items = []
        seen = set()
        for video_id, title in videos:
            for order in ("relevance", "time"):
                resp = http.get(f"{YOUTUBE_API}/commentThreads", params={
                    "key": settings.youtube_api_key, "videoId": video_id, "part": "snippet",
                    "maxResults": comment_limit, "order": order, "textFormat": "plainText"})
                if resp.status_code == 403 and "commentsDisabled" in resp.text:
                    continue
                resp.raise_for_status()
                for thread in resp.json().get("items", []):
                    comment = thread["snippet"]["topLevelComment"]
                    if comment["id"] in seen:
                        continue
                    seen.add(comment["id"])
                    snippet = comment["snippet"]
                    items.append(Item(
                        comment["id"],
                        f"https://www.youtube.com/watch?v={video_id}&lc={comment['id']}",
                        title,
                        snippet["textDisplay"],
                        datetime.fromisoformat(snippet["publishedAt"]),
                    ))
    return items
