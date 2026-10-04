from datetime import UTC, datetime

import httpx
import pytest
import respx

from researcher.connectors.base import fetch, fetch_context
from researcher.models import Source

FEED_URL = "https://forum.example/c/support/7.rss"


def source(limit: int = 30) -> Source:
    return Source(kind="discourse", name="Forum", config={"feed_url": FEED_URL, "limit": limit})


def feed(items: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel><title>Support</title>{items}</channel></rss>"
    )


@respx.mock
def test_rss_item_maps_to_item_and_topic_id_stays_stable():
    route = respx.get(FEED_URL).mock(side_effect=[
        httpx.Response(200, text=feed("""
            <item><title>App sync fails</title>
            <link>https://forum.example/t/old-slug/123</link>
            <guid>https://forum.example/t/old-slug/123</guid>
            <pubDate>Tue, 14 Nov 2023 22:13:20 GMT</pubDate>
            <content:encoded><![CDATA[<p>My files do not sync.</p>]]></content:encoded>
            </item>""")),
        httpx.Response(200, text=feed("""
            <item><title>App sync fails</title>
            <link>https://forum.example/t/new-slug/123</link>
            <guid>https://forum.example/t/old-slug/123</guid>
            <description><![CDATA[<p>My files do not sync.</p>]]></description>
            </item>""")),
    ])

    first = fetch(source())[0]
    second = fetch(source())[0]

    assert first.external_id == second.external_id == "123"
    assert first.url == "https://forum.example/t/old-slug/123"
    assert first.title == "App sync fails"
    assert first.text == "My files do not sync."
    assert first.published_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert first.metadata == {}
    assert route.call_count == 2

    respx.get("https://forum.example/t/123.json").mock(return_value=httpx.Response(200, json={
        "post_stream": {"posts": [
            {"username": "author", "cooked": "<p>My files do not sync.</p>"},
            {"username": "reader", "cooked": "<p>I see the same failure.</p>"},
        ]},
    }))
    assert fetch_context(source(), first.external_id) == "[reply by reader] I see the same failure."


@respx.mock
def test_rss_limit_skips_incomplete_and_duplicate_items():
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=feed("""
        <item><title>Missing link</title><description>Ignore me</description></item>
        <item><title>First</title><link>https://forum.example/t/first/1</link></item>
        <item><title>Repeated</title><link>https://forum.example/t/renamed/1</link></item>
        <item><title>Second</title><link>https://forum.example/t/second/2</link></item>
        <item><title>Third</title><link>https://forum.example/t/third/3</link></item>""")))

    assert [item.external_id for item in fetch(source(limit=2))] == ["1", "2"]


@respx.mock
def test_rss_empty_feed_and_fallback_guid():
    route = respx.get(FEED_URL).mock(side_effect=[
        httpx.Response(200, text=feed()),
        httpx.Response(200, text=feed("""
            <item><title>Without topic ID</title>
            <link>https://forum.example/custom/path</link>
            <guid isPermaLink="false">stable-guid-42</guid></item>""")),
    ])

    assert fetch(source()) == []
    assert fetch(source())[0].external_id == "stable-guid-42"
    assert fetch_context(source(), "stable-guid-42") == ""
    assert route.call_count == 2


@respx.mock
def test_rss_http_error_is_propagated():
    respx.get(FEED_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        fetch(source())
