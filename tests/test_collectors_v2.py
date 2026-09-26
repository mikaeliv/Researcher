import httpx
import respx

from researcher.connectors import hackernews
from researcher.connectors.base import fetch, fetch_context
from researcher.models import Source


@respx.mock
def test_hackernews_fetches_original_before_comments(monkeypatch):
    monkeypatch.setattr(hackernews.settings, "source_lookback_days", 0)
    respx.get(f"{hackernews.API_URL}/askstories.json").mock(
        return_value=httpx.Response(200, json=[10])
    )
    story = respx.get(f"{hackernews.API_URL}/item/10.json").mock(
        return_value=httpx.Response(200, json={
            "id": 10,
            "type": "story",
            "title": "Ask HN: How do you track family appointments?",
            "text": "I copy appointments from email into a calendar every week.",
            "time": 1_700_000_000,
            "score": 42,
            "descendants": 1,
            "kids": [11],
        })
    )
    comment = respx.get(f"{hackernews.API_URL}/item/11.json").mock(
        return_value=httpx.Response(200, json={
            "id": 11,
            "type": "comment",
            "text": "We have the same problem.",
            "kids": [12],
        })
    )
    nested = respx.get(f"{hackernews.API_URL}/item/12.json").mock(
        return_value=httpx.Response(200, json={
            "id": 12, "type": "comment", "text": "Our workaround is a shared spreadsheet.",
        })
    )
    source = Source(kind="hackernews", name="Hacker News / Ask HN", config={"limit": 1})

    items = fetch(source)

    assert items[0].title == "Ask HN: How do you track family appointments?"
    assert items[0].text == "I copy appointments from email into a calendar every week."
    assert items[0].metadata == {"score": 42, "comments": 1}
    assert story.call_count == 1
    assert not comment.called
    assert not nested.called

    context = fetch_context(source, "10")

    assert "same problem" in context
    assert "shared spreadsheet" in context
    assert story.call_count == 2
    assert comment.called
    assert nested.called


@respx.mock
def test_discourse_uses_generic_base_url_category_and_delays_replies():
    base_url = "https://forum.example"
    respx.get(f"{base_url}/c/ideas/7.json").mock(return_value=httpx.Response(200, json={
        "topic_list": {"topics": [{
            "id": 21,
            "slug": "shared-calendar",
            "title": "Shared calendar automation",
            "created_at": "2023-11-14T22:13:20Z",
            "views": 100,
            "reply_count": 2,
        }]},
    }))
    respx.get(f"{base_url}/raw/21").mock(
        return_value=httpx.Response(200, text="I manually copy events for my family.")
    )
    replies = respx.get(f"{base_url}/t/21.json").mock(return_value=httpx.Response(200, json={
        "post_stream": {"posts": [
            {"username": "author", "raw": "I manually copy events for my family."},
            {"username": "reader", "cooked": "<p>I need this too.</p>"},
        ]},
    }))
    source = Source(kind="discourse", name="Forum / Ideas", config={
        "base_url": base_url, "category": "ideas/7", "lookback_days": 0,
    })

    items = fetch(source)

    assert items[0].text == "I manually copy events for my family."
    assert items[0].metadata == {"views": 100, "replies": 2}
    assert not replies.called

    assert fetch_context(source, "21") == "[reply by reader] I need this too."
    assert replies.called


@respx.mock
def test_lemmy_uses_community_and_delays_comments():
    base_url = "https://lemmy.example"
    posts = respx.get(f"{base_url}/api/v3/post/list").mock(return_value=httpx.Response(200, json={
        "posts": [{
            "post": {
                "id": 31,
                "name": "How do you track recurring chores?",
                "body": "Our household loses track of recurring chores every week.",
                "published": "2023-11-14T22:13:20Z",
                "ap_id": "https://lemmy.example/post/31",
            },
            "community": {"name": "productivity"},
            "counts": {"score": 9, "comments": 1},
        }],
    }))
    comments = respx.get(f"{base_url}/api/v3/comment/list").mock(
        return_value=httpx.Response(200, json={
            "comments": [{"comment": {"content": "We use a spreadsheet workaround."}}],
        })
    )
    source = Source(kind="lemmy", name="Lemmy / Productivity", config={
        "base_url": base_url, "community": "productivity", "lookback_days": 0,
    })

    items = fetch(source)

    assert items[0].text == "Our household loses track of recurring chores every week."
    assert items[0].metadata["community"] == "productivity"
    assert posts.calls[0].request.url.params["community_name"] == "productivity"
    assert not comments.called

    assert fetch_context(source, "31") == "[comment] We use a spreadsheet workaround."
    assert comments.called
