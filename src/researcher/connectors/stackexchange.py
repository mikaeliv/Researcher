import time
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from researcher.models import Source

from .base import Item, client

STACK_EXCHANGE_URL = "https://api.stackexchange.com/2.3/questions"


def fetch_stackexchange(source: Source) -> list[Item]:
    tags = source.config.get("tags") or [source.config.get("tagged")]
    if isinstance(tags, str):
        tags = [tags]
    tags = list(dict.fromkeys(tag for tag in tags if tag)) or [None]

    questions = {}
    with client() as http:
        for tag in tags:
            params = {"site": source.config.get("site", "stackoverflow"), "pagesize": 50,
                      "sort": "creation", "order": "desc", "filter": "withbody"}
            if tag:
                params["tagged"] = tag
            if source.cursor:
                params["fromdate"] = int(source.cursor)
            response = http.get(STACK_EXCHANGE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
            if "error_id" in payload:
                raise ValueError(payload.get("error_message", "Stack Exchange error"))
            questions.update((q["question_id"], q) for q in payload["items"])
            if backoff := payload.get("backoff"):
                time.sleep(backoff)

    return [Item(str(q["question_id"]), q["link"],
                 BeautifulSoup(q["title"], "html.parser").get_text(),
                 BeautifulSoup(q.get("body", ""), "html.parser").get_text(" ", strip=True),
                 datetime.fromtimestamp(q["creation_date"], UTC)) for q in questions.values()]
