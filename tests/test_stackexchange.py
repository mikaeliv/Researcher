from datetime import UTC, datetime

import httpx
import respx

from researcher.connectors import stackexchange
from researcher.models import Source


@respx.mock
def test_fetches_tags_separately_deduplicates_and_honors_backoff(monkeypatch):
    route = respx.get(stackexchange.STACK_EXCHANGE_URL).mock(side_effect=[
        httpx.Response(200, json={
            "items": [{
                "question_id": 1,
                "link": "https://stackoverflow.com/q/1",
                "title": "Android &amp; Kotlin",
                "body": "<p>First body</p>",
                "creation_date": 1_700_000_001,
                "tags": ["android", "kotlin"],
            }],
            "backoff": 2,
        }),
        httpx.Response(200, json={
            "items": [{
                "question_id": 1,
                "link": "https://stackoverflow.com/q/1",
                "title": "Android &amp; Kotlin",
                "body": "<p>First body</p>",
                "creation_date": 1_700_000_001,
                "tags": ["android", "kotlin"],
            }, {
                "question_id": 2,
                "link": "https://stackoverflow.com/q/2",
                "title": "Kotlin question",
                "body": "<p>Second <b>body</b></p>",
                "creation_date": 1_700_000_002,
                "tags": ["kotlin"],
            }],
        }),
    ])
    waits = []
    monkeypatch.setattr(stackexchange.time, "sleep", waits.append)
    source = Source(kind="stackexchange", name="Stack Overflow", cursor="1700000000",
                    config={"site": "stackoverflow", "tags": ["android", "kotlin"]})

    items = stackexchange.fetch_stackexchange(source)

    assert [call.request.url.params["tagged"] for call in route.calls] == ["android", "kotlin"]
    assert all(call.request.url.params["filter"] == "withbody" for call in route.calls)
    assert all(call.request.url.params["pagesize"] == "50" for call in route.calls)
    assert all(call.request.url.params["fromdate"] == "1700000000" for call in route.calls)
    assert all("key" not in call.request.url.params for call in route.calls)
    assert waits == [2]
    assert [item.external_id for item in items] == ["1", "2"]
    assert items[0].title == "Android & Kotlin"
    assert items[1].text == "Second body"
    assert items[1].published_at == datetime.fromtimestamp(1_700_000_002, UTC)
