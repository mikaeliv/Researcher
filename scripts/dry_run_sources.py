"""Evaluate Sources v2 with production filtering and analysis without collecting data."""
import argparse
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from researcher.ai import Classification, Finding, analyze
from researcher.connectors.base import Item, fetch, fetch_context
from researcher.db import SessionLocal
from researcher.models import AiUsage, Source
from researcher.pipeline import (
    accepts_as_evidence,
    add_context,
    analysis_input,
    item_raw_text,
    likely_candidate,
    normalize,
)

V2_SOURCES = (
    "Hacker News / Ask HN",
    "Home Assistant / Feature Requests",
    "Lemmy / Productivity",
    "Lemmy / Selfhosted",
    "Lemmy / Apps",
    "Stack Exchange / Personal Finance",
    "Stack Exchange / Home Improvement",
)


@dataclass
class Stats:
    source: str
    fetched: int = 0
    filtered: int = 0
    analyzed: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: int = 0
    ai_usage_calls: int = 0
    classifications: Counter = field(default_factory=Counter)

    def merge(self, other: "Stats") -> None:
        for name in (
            "fetched", "filtered", "analyzed", "accepted", "rejected", "errors",
            "ai_usage_calls",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.classifications.update(other.classifications)


def load_sources(db: Session, source_name: str | None = None) -> tuple[list[Source], list[str]]:
    names = (source_name,) if source_name else V2_SOURCES
    rows = db.scalars(
        select(Source).where(Source.enabled.is_(True), Source.name.in_(names))
    ).all()
    by_name = {source.name: source for source in rows}
    sources = [by_name[name] for name in names if name in by_name]
    for source in sources:
        db.expunge(source)
    return sources, [name for name in names if name not in by_name]


def _print_item(emit: Callable[[str], None], item: Item) -> None:
    emit(f"\n[{item.external_id}]")
    emit(f"title: {item.title}")


def _print_finding(emit: Callable[[str], None], finding: Finding, accepted: bool) -> None:
    emit(f"classification: {finding.classification.value}")
    emit(f"product_solvable: {str(finding.product_solvable).lower()}")
    emit(f"confidence: {finding.confidence:.2f}")
    emit(f"accepted: {str(accepted).lower()}")
    emit(f"problem: {finding.problem}")
    emit(f"audience: {finding.audience}")
    for name in ("workaround", "frequency", "loss"):
        if value := getattr(finding, name):
            emit(f"{name}: {value}")
    emit(f"willingness_to_pay: {str(finding.willingness_to_pay).lower()}")


def _print_summary(emit: Callable[[str], None], stats: Stats) -> None:
    emit("\n--- Source summary ---")
    emit(f"Source: {stats.source}")
    emit(f"Fetched: {stats.fetched}")
    emit(f"Pre-LLM filtered: {stats.filtered}")
    emit(f"Analyzed: {stats.analyzed}")
    emit(f"Accepted: {stats.accepted}")
    emit(f"Rejected: {stats.rejected}")
    emit(f"Errors: {stats.errors}")
    for classification in Classification:
        emit(f"{classification.value}: {stats.classifications[classification.value]}")


def _commit_ai_usage_only(db: Session) -> None:
    new_objects = tuple(db.new)
    if (
        any(not isinstance(obj, AiUsage) for obj in new_objects)
        or tuple(db.dirty)
        or tuple(db.deleted)
    ):
        db.rollback()
        raise RuntimeError("Dry-run blocked a non-AiUsage database change")
    db.commit()


def run_source(
    db: Session,
    source: Source,
    limit: int,
    emit: Callable[[str], None] = print,
) -> Stats:
    stats = Stats(source.name)
    emit(f"\n=== {source.name} ===")
    try:
        items = fetch(source)[:limit]
        stats.fetched = len(items)
    except Exception as exc:  # noqa: BLE001 - one source must not stop the experiment
        stats.errors += 1
        emit(f"ERROR: {exc}")
        _print_summary(emit, stats)
        return stats

    for item in items:
        _print_item(emit, item)
        raw_text = item_raw_text(item)
        if not likely_candidate(normalize(item.title + "\n" + raw_text)):
            stats.filtered += 1
            emit("cheap_filter: FILTERED")
            emit("LLM: not called")
            continue
        emit("cheap_filter: PASS")
        try:
            context = fetch_context(source, item.external_id)
            emit(f"context_chars: {len(context)}")
            finding = analyze(db, analysis_input(item.title, add_context(raw_text, context)))
            _commit_ai_usage_only(db)
            accepted = accepts_as_evidence(finding)
            stats.analyzed += 1
            stats.ai_usage_calls += 1
            stats.classifications[finding.classification.value] += 1
            if accepted:
                stats.accepted += 1
            else:
                stats.rejected += 1
            _print_finding(emit, finding, accepted)
        except Exception as exc:  # noqa: BLE001 - one item must not stop the experiment
            db.rollback()
            stats.errors += 1
            emit(f"ERROR: {exc}")
    _print_summary(emit, stats)
    return stats


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=V2_SOURCES)
    parser.add_argument("--limit", type=_positive, default=10)
    args = parser.parse_args(argv)

    total = Stats("TOTAL")
    with SessionLocal() as db:
        sources, missing = load_sources(db, args.source)
        for source in sources:
            total.merge(run_source(db, source, args.limit))
        for name in missing:
            stats = Stats(name, errors=1)
            print(f"\n=== {name} ===")
            print("ERROR: active Source not found in database")
            _print_summary(print, stats)
            total.merge(stats)

    print("\n=== TOTAL ===")
    print(f"Fetched: {total.fetched}")
    print(f"Pre-LLM filtered: {total.filtered}")
    print(f"Analyzed: {total.analyzed}")
    print(f"Accepted: {total.accepted}")
    print(f"Rejected: {total.rejected}")
    print(f"Errors: {total.errors}")
    analyzed_rate = total.accepted / total.analyzed if total.analyzed else 0
    fetched_rate = total.accepted / total.fetched if total.fetched else 0
    print(f"Acceptance rate among analyzed: {analyzed_rate:.1%}")
    print(f"Acceptance rate among fetched: {fetched_rate:.1%}")
    print(f"AiUsage calls: {total.ai_usage_calls}")


if __name__ == "__main__":
    main()
