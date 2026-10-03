"""Repeat production analysis on five frozen source inputs."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from researcher.ai import analyze
from researcher.connectors.base import fetch, fetch_context
from researcher.db import SessionLocal
from researcher.models import Source
from researcher.pipeline import accepts_as_evidence, add_context, analysis_input, item_raw_text

TARGETS = [
    {
        "source": "Stack Exchange / Personal Finance",
        "external_id": "169996",
        "title": "How to invest long-term when moving countries frequently?",
    },
    {
        "source": "Stack Exchange / Personal Finance",
        "external_id": "170016",
        "title": "Has anyone had a PayPal dispute reopened for manual review?",
    },
    {
        "source": "Lemmy / Selfhosted",
        "external_id": "52489909",
        "title": "Joplin sync",
    },
    {
        "source": "Lemmy / Selfhosted",
        "external_id": "52486918",
        "title": "Phishing / spam detection with local LLMs?",
    },
    {
        "source": "Stack Exchange / Personal Finance",
        "external_id": "170030",
        "title": "Do any credit cards provide third-party liability insurance for rental cars?",
    },
]
FIXTURE = Path(__file__).resolve().parents[1] / ".var/analyze_stability_corpus.json"


def freeze() -> None:
    records = []
    with SessionLocal() as db:
        items_by_source = {}
        for target in TARGETS:
            source_name = target["source"]
            if source_name not in items_by_source:
                source = db.scalar(
                    select(Source).where(Source.name == source_name, Source.enabled.is_(True))
                )
                if source is None:
                    raise RuntimeError(f"Active Source not found: {source_name}")
                items_by_source[source_name] = (source, fetch(source))

            source, items = items_by_source[source_name]
            matches = [item for item in items if item.external_id == target["external_id"]]
            if len(matches) != 1:
                raise RuntimeError(
                    f"Expected one item {target['external_id']} in {source_name}; found {len(matches)} "
                    "in the normal fetch. A source-specific external-ID lookup helper is needed."
                )
            item = matches[0]
            if item.title != target["title"]:
                raise RuntimeError(
                    f"Title mismatch for {source_name} / {item.external_id}: "
                    f"expected {target['title']!r}, got {item.title!r}"
                )
            context = fetch_context(source, item.external_id)
            frozen_input = analysis_input(item.title, add_context(item_raw_text(item), context))
            records.append({
                "publication_id": None,  # Keep the unchanged experiment runner compatible.
                "source": source_name,
                "external_id": item.external_id,
                "title": item.title,
                "input": frozen_input,
                "input_chars": len(frozen_input),
                "analyzed_chars": min(len(frozen_input), 6000),
                "context_chars": len(context),
            })

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(records)} frozen inputs to .var/analyze_stability_corpus.json\n")
    for record in records:
        print(
            f"[{record['external_id']}] {record['source']} | {record['title']} "
            f"input_chars={record['input_chars']} analyzed_chars={record['analyzed_chars']} "
            f"context_chars={record['context_chars']}"
        )


def experiment(runs: int) -> None:
    if not FIXTURE.exists():
        raise SystemExit(
            "Frozen fixture is missing. First run: python scripts/analyze_stability.py --freeze"
        )
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print("STABILITY EXPERIMENT")
    print("No Publications, Evidence, Clusters or AiUsage records will be persisted.")

    critical_count = 0
    summaries = []
    for record in records:
        print("\n" + "=" * 100)
        print(record["title"])
        print("=" * 100)
        results = []
        for run in range(1, runs + 1):
            with SessionLocal() as db:
                try:
                    finding = analyze(db, record["input"])
                    accepted = accepts_as_evidence(finding)
                    result = {
                        "publication_id": record["publication_id"],
                        "title": record["title"],
                        "run": run,
                        "classification": finding.classification.value,
                        "product_solvable": finding.product_solvable,
                        "contains_pain": finding.contains_pain,
                        "confidence": finding.confidence,
                        "accepted": accepted,
                        "problem": finding.problem,
                    }
                finally:
                    db.rollback()
            results.append(result)
            print(
                f"\nrun={run} | classification={result['classification']} "
                f"| product_solvable={str(result['product_solvable']).lower()} "
                f"| contains_pain={str(result['contains_pain']).lower()} "
                f"| confidence={result['confidence']} "
                f"| accepted={str(result['accepted']).lower()}"
            )
            print(f"problem: {result['problem']}")
        critical = len({result["accepted"] for result in results}) > 1
        critical_count += critical
        summaries.append((record["title"], results, "CRITICAL" if critical else "STABLE_GATE"))

    print("\nAGGREGATE SUMMARY")
    for title, results, status in summaries:
        print(f"\n{title}")
        for field in ("classification", "product_solvable", "contains_pain", "accepted"):
            print(f"  {field} variants: {sorted({result[field] for result in results})}")
        print(f"  status: {status}")
    print(f"\nRecords: {len(records)}")
    print(f"Runs per record: {runs}")
    print(f"Total calls: {len(records) * runs}")
    print(f"Critical records: {critical_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true", help="freeze current source inputs")
    parser.add_argument("--runs", type=int, default=4, help="analysis calls per input (default: 4)")
    args = parser.parse_args()
    if args.freeze:
        freeze()
    else:
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        experiment(args.runs)


if __name__ == "__main__":
    main()
