from datetime import UTC, datetime

from bs4 import BeautifulSoup

from researcher.models import Source

from .base import Item, client


def fetch_stackexchange(source: Source) -> list[Item]:
    params = {"site": source.config.get("site", "stackoverflow"), "pagesize": 100,
              "sort": "creation", "order": "desc", "filter": "withbody"}
    if source.config.get("tagged"):
        params["tagged"] = source.config["tagged"]
    if source.cursor:
        params["fromdate"] = int(source.cursor)
    with client() as http:
        response = http.get("https://api.stackexchange.com/2.3/questions", params=params)
        response.raise_for_status()
        payload = response.json()
    if "error_id" in payload:
        raise ValueError(payload.get("error_message", "Stack Exchange error"))
    return [Item(str(q["question_id"]), q["link"], BeautifulSoup(q["title"], "html.parser").get_text(),
                 BeautifulSoup(q.get("body", ""), "html.parser").get_text(" ", strip=True),
                 datetime.fromtimestamp(q["creation_date"], UTC)) for q in payload["items"]]

