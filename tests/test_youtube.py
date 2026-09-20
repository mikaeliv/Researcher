from datetime import UTC, datetime

import httpx
import respx

from researcher.connectors import youtube
from researcher.models import Source


@respx.mock
def test_fetches_recent_channel_videos_and_deduplicates_comment_orders(monkeypatch):
    monkeypatch.setattr(youtube.settings, "youtube_api_key", "test-key")
    respx.get(f"{youtube.YOUTUBE_API}/channels").mock(return_value=httpx.Response(200, json={
        "items": [{
            "snippet": {"title": "Example channel"},
            "contentDetails": {"relatedPlaylists": {"uploads": "uploads-id"}},
        }],
    }))
    respx.get(f"{youtube.YOUTUBE_API}/playlistItems").mock(return_value=httpx.Response(200, json={
        "items": [{
            "snippet": {"title": "Recent video"},
            "contentDetails": {"videoId": "video-1"},
        }],
    }))
    comments = respx.get(f"{youtube.YOUTUBE_API}/commentThreads").mock(side_effect=[
        httpx.Response(200, json={"items": [_thread("comment-1"), _thread("comment-2")]}),
        httpx.Response(200, json={"items": [_thread("comment-2"), _thread("comment-3")]}),
    ])
    source = Source(kind="youtube", name="YouTube", config={
        "channel_handles": ["@Example"],
        "videos_per_channel": 5,
        "comments_per_order": 25,
    })

    items = youtube.fetch_youtube(source)

    assert [item.external_id for item in items] == ["comment-1", "comment-2", "comment-3"]
    assert items[0].title == "Example channel: Recent video"
    assert items[0].url.endswith("watch?v=video-1&lc=comment-1")
    assert items[0].published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert [call.request.url.params["order"] for call in comments.calls] == ["relevance", "time"]


def _thread(comment_id: str) -> dict:
    return {
        "snippet": {
            "topLevelComment": {
                "id": comment_id,
                "snippet": {
                    "textDisplay": f"Text for {comment_id}",
                    "publishedAt": "2026-01-02T03:04:05Z",
                },
            },
        },
    }
