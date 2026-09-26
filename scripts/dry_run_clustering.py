"""Simulate clustering in memory without changing production clustering data."""

import sys
from collections import Counter
from dataclasses import dataclass, field
from math import sqrt

from sqlalchemy import select

from researcher.ai import same_problem
from researcher.config import settings
from researcher.db import SessionLocal
from researcher.models import Evidence, Publication


@dataclass(frozen=True)
class EvidenceItem:
    id: int
    problem: str
    audience: str
    embedding: tuple[float, ...]
    source_id: int


@dataclass(frozen=True)
class VirtualMember:
    evidence: EvidenceItem
    distance: float | None


@dataclass
class VirtualCluster:
    representative: EvidenceItem
    members: list[VirtualMember] = field(default_factory=list)


def cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denominator = sqrt(sum(value * value for value in left)) * sqrt(
        sum(value * value for value in right)
    )
    if not denominator:
        return float("inf")
    return 1 - sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def load_evidence() -> list[EvidenceItem]:
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Evidence.id,
                Evidence.problem,
                Evidence.audience,
                Evidence.embedding,
                Publication.source_id,
            )
            .join(Publication, Evidence.publication_id == Publication.id)
            .where(Evidence.embedding.is_not(None))
            .order_by(Evidence.id)
        ).all()
    return [
        EvidenceItem(evidence_id, problem, audience, tuple(embedding), source_id)
        for evidence_id, problem, audience, embedding, source_id in rows
    ]


def simulate(evidence_items: list[EvidenceItem]) -> tuple[list[VirtualCluster], Counter]:
    clusters: list[VirtualCluster] = []
    stats = Counter()
    with SessionLocal() as ai_db:
        for evidence in evidence_items:
            stats["processed"] += 1
            # ponytail: O(n²) is intentional for 189 rows; use vector search if this becomes large.
            candidates = sorted(
                (
                    (cosine_distance(evidence.embedding, cluster.representative.embedding), cluster)
                    for cluster in clusters
                ),
                key=lambda item: item[0],
            )[: settings.cluster_candidate_top_k]

            attached = False
            for distance, cluster in candidates:
                if distance >= settings.cluster_candidate_threshold:
                    continue
                stats["calls"] += 1
                try:
                    matches = same_problem(
                        ai_db,
                        evidence.problem,
                        evidence.audience,
                        cluster.representative.problem,
                        cluster.representative.audience,
                        cluster.representative.problem,
                    )
                    ai_db.commit()  # Persists only AiUsage added by same_problem().
                except Exception as exc:  # noqa: BLE001 - mirror production fail-closed behavior
                    stats["errors"] += 1
                    try:
                        ai_db.commit()  # Preserve usage if the response failed validation.
                    except Exception:  # noqa: BLE001 - rollback any failed accounting transaction
                        ai_db.rollback()
                    print(
                        f"same_problem error for Evidence {evidence.id}: {exc}",
                        file=sys.stderr,
                    )
                    break
                if matches:
                    stats["true"] += 1
                    cluster.members.append(VirtualMember(evidence, distance))
                    attached = True
                    break
                stats["false"] += 1

            if not attached:
                clusters.append(VirtualCluster(evidence, [VirtualMember(evidence, None)]))
    return clusters, stats


def distinct_sources(cluster: VirtualCluster) -> int:
    return len({member.evidence.source_id for member in cluster.members})


def print_report(clusters: list[VirtualCluster], stats: Counter) -> None:
    merged = [cluster for cluster in clusters if len(cluster.members) >= 2]
    eligible = [
        (number, cluster)
        for number, cluster in enumerate(clusters, 1)
        if len(cluster.members) >= settings.min_cluster_evidence
        and distinct_sources(cluster) >= settings.min_cluster_sources
    ]
    print(f"Evidence processed: {stats['processed']}")
    print(f"Virtual clusters: {len(clusters)}")
    print(f"Merged clusters (size >= 2): {len(merged)}")
    print(f"Singleton clusters: {len(clusters) - len(merged)}")
    print(f"Evidence in merged clusters: {sum(len(cluster.members) for cluster in merged)}")
    print(f"Largest cluster: {max((len(cluster.members) for cluster in clusters), default=0)}")
    print(f"same_problem calls: {stats['calls']}")
    print(f"same_problem True: {stats['true']}")
    print(f"same_problem False: {stats['false']}")
    print(f"same_problem errors: {stats['errors']}")
    print(
        "Clusters with >= 2 distinct sources: "
        + str(sum(distinct_sources(cluster) >= 2 for cluster in clusters))
    )

    print("\n=== Merged clusters ===")
    for number, cluster in enumerate(clusters, 1):
        if len(cluster.members) < 2:
            continue
        print(f"\n=== Cluster {number} ===")
        print(f"size: {len(cluster.members)}")
        print(f"distinct sources: {distinct_sources(cluster)}")
        for member in cluster.members:
            evidence = member.evidence
            marker = "representative" if member.distance is None else f"distance={member.distance:.4f}"
            print(f"\n[{evidence.id}] {marker}")
            print(evidence.audience)
            print(evidence.problem)

    print("\n=== Potentially eligible clusters (evidence requirements only) ===")
    if not eligible:
        print("none")
    for number, cluster in eligible:
        print(
            f"Cluster {number}: size={len(cluster.members)}, "
            f"distinct sources={distinct_sources(cluster)}"
        )


def main() -> None:
    evidence_items = load_evidence()
    clusters, stats = simulate(evidence_items)
    print_report(clusters, stats)


if __name__ == "__main__":
    main()
