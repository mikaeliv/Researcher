"""Синхронизировать sources.json с БД, обновляя и переименовывая записи."""
import json
import sys
from pathlib import Path

from sqlalchemy import select

from researcher.db import SessionLocal
from researcher.models import Source
from researcher.source_groups import source_group_key

SOURCES_PATH = Path(__file__).resolve().parents[1] / "sources.json"


def main() -> None:
    """Создать или обновить источники одним commit после проверки типов."""
    with open(SOURCES_PATH, encoding="utf-8") as stream:
        sources = json.load(stream)
    allowed = {
        "rss", "stackexchange", "reddit", "youtube", "appstore",
        "hackernews", "discourse", "lemmy",
    }
    with SessionLocal() as db:
        names = {data["name"] for data in sources}
        if "--disable-unlisted" in sys.argv[1:]:
            # Не удаляем старые источники: на них уже ссылаются публикации.
            for source in db.scalars(select(Source).where(Source.name.not_in(names))).all():
                source.enabled = False
        for data in sources:
            if data["kind"] not in allowed:
                raise ValueError("Unknown kind " + data["kind"])
            source = db.scalar(select(Source).where(Source.name == data["name"]))
            if source is None and data.get("previous_name"):
                source = db.scalar(select(Source).where(Source.name == data["previous_name"]))
            if source is None:
                source = Source(
                    kind=data["kind"], name=data["name"], config=data["config"],
                    enabled=data.get("enabled", True),
                )
                db.add(source)
            else:
                source.kind, source.name, source.config = data["kind"], data["name"], data["config"]
                source.enabled = data.get("enabled", True)
            source.source_group_key = data.get("source_group_key") or source_group_key(
                data["kind"], data["name"], data["config"],
            )
            if not isinstance(source.source_group_key, str) or not source.source_group_key.strip():
                raise ValueError("source_group_key must be nonempty text")
        db.commit()


if __name__ == "__main__":
    main()
