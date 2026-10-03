"""Repeat production analysis on five frozen publication inputs."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from researcher.ai import analyze
from researcher.db import SessionLocal
from researcher.models import Publication
from researcher.pipeline import accepts_as_evidence, analysis_input

TITLES = (
    "How to invest long-term when moving countries frequently?",
    "Has anyone had a PayPal dispute reopened for manual review?",
    "Joplin sync",
    "Phishing / spam detection with local LLMs?",
    "Do any credit cards provide third-party liability insurance for rental cars?",
)
FIXTURE = Path(__file__).resolve().parents[1] / ".var/analyze_stability_corpus.json"


def freeze() -> None:
    records = []
    with SessionLocal() as db:
        for title in TITLES:
            publications = db.scalars(select(Publication).where(Publication.title == title)).all()
            if len(publications) != 1:
                raise SystemExit(
                    f"Expected exactly one Publication titled {title!r}; found {len(publications)}"
                )
            publication = publications[0]
            frozen_input = analysis_input(publication.title, publication.raw_text)
            records.append({
                "publication_id": publication.id,
                "title": publication.title,
                "input": frozen_input,
                "input_chars": len(frozen_input),
                "analyzed_chars": min(len(frozen_input), 6000),
            })

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Saved 5 frozen inputs to .var/analyze_stability_corpus.json\n")
    for record in records:
        print(
            f"[{record['publication_id']}] {record['title']} "
            f"input_chars={record['input_chars']} analyzed_chars={record['analyzed_chars']}"
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
    parser.add_argument("--freeze", action="store_true", help="freeze current Publication inputs")
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
