"""Load explicitly chosen sources from JSON. Existing entries are updated or renamed."""
import json
import sys

from sqlalchemy import select

from researcher.db import SessionLocal
from researcher.models import Source


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as stream:
        sources = json.load(stream)
    allowed = {"rss", "stackexchange", "reddit", "youtube", "appstore"}
    with SessionLocal() as db:
        for data in sources:
            if data["kind"] not in allowed:
                raise ValueError("Unknown kind " + data["kind"])
            source = db.scalar(select(Source).where(Source.name == data["name"]))
            if source is None and data.get("previous_name"):
                source = db.scalar(select(Source).where(Source.name == data["previous_name"]))
            if source is None:
                source = Source(kind=data["kind"], name=data["name"], config=data["config"])
                db.add(source)
            else:
                source.kind, source.name, source.config = data["kind"], data["name"], data["config"]
        db.commit()


if __name__ == "__main__":
    main()
