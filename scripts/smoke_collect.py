"""Fetch a bounded source sample without writing to the database or calling the LLM."""
import argparse

from sqlalchemy import select

from researcher.connectors.base import fetch, fetch_context
from researcher.db import SessionLocal
from researcher.models import Source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_name")
    parser.add_argument("--context", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        source = db.scalar(select(Source).where(Source.name == args.source_name))
        if source is None:
            raise SystemExit(f"Unknown source: {args.source_name}")
        items = fetch(source)
        print(f"Fetched {len(items)} items from {source.name}")
        for item in items[:5]:
            print(f"- {item.external_id}: {item.title[:120]}")
        if args.context and items:
            context = fetch_context(source, items[0].external_id)
            print(f"Context characters for {items[0].external_id}: {len(context)}")


if __name__ == "__main__":
    main()
