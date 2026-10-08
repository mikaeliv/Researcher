"""Controlled Opportunity simulation: read-only DB, virtual membership, local profile cache."""

import argparse
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite, sqrt
from pathlib import Path

from sqlalchemy import func, select, text

from researcher.ai import embed
from researcher.config import settings
from researcher.db import SessionLocal
from researcher.models import AiUsage, Cluster, Evidence, Publication, Source
from researcher.opportunities import (
    OpportunityMatch,
    OpportunityProfile,
    OpportunitySummary,
    build_cluster_opportunity_profile,
    build_opportunity,
    embedding_input,
    same_opportunity,
    summarize_opportunity,
    validate_opportunity,
)

logger = logging.getLogger(__name__)
CACHE_VERSION = 1


@dataclass(frozen=True)
class ClusterItem:
    id: int
    title: str
    audience: str
    description: str
    evidence_count: int
    source_groups: frozenset[str]


@dataclass(frozen=True)
class ProfiledCluster:
    cluster: ClusterItem
    profile: OpportunityProfile
    embedding: tuple[float, ...]

    def ai_input(self) -> dict:
        return {
            "cluster_id": self.cluster.id, "problem": self.cluster.title,
            "audience": self.cluster.audience, "description": self.cluster.description,
            "profile": self.profile.model_dump(),
        }


@dataclass(frozen=True)
class Member:
    item: ProfiledCluster
    distance: float | None = None
    confidence: float | None = None
    reason: str = "Initial singleton"


@dataclass
class VirtualOpportunity:
    profile: OpportunityProfile
    embedding: tuple[float, ...]
    members: list[Member]
    representative_cluster_id: int | None = None
    summary: OpportunitySummary | None = None


@dataclass(frozen=True)
class RejectedCandidate:
    cluster_id: int
    opportunity_cluster_ids: tuple[int, ...]
    distance: float
    match: OpportunityMatch | None
    reason: str
    too_broad: bool | None
    cluster_problem: str
    opportunity_pain: str


@dataclass
class CandidateDiagnostic:
    cluster: ClusterItem
    opportunity_cluster_ids: tuple[int, ...]
    opportunity_pain: str
    opportunity_source_groups: frozenset[str]
    distance: float
    match: OpportunityMatch | None = None
    error: str | None = None


def parse_source_groups(value: str) -> set[str]:
    groups = {key.strip() for key in value.split(",")}
    if "" in groups:
        raise argparse.ArgumentTypeError("--source-groups requires nonempty comma-separated keys")
    return groups


def load_clusters(limit: int | None = None,
                  requested_source_groups: set[str] | None = None) -> list[ClusterItem]:
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            query = select(Cluster).order_by(Cluster.id)
            if requested_source_groups is not None:
                known_groups = set(db.scalars(select(Source.source_group_key).distinct()).all())
                unknown = requested_source_groups - known_groups
                if unknown:
                    raise ValueError("Unknown source_group_key: " + ", ".join(sorted(unknown)))
                query = (
                    query.join(Evidence, Evidence.cluster_id == Cluster.id)
                    .join(Publication, Publication.id == Evidence.publication_id)
                    .join(Source, Source.id == Publication.source_id)
                    .where(Source.source_group_key.in_(requested_source_groups))
                    .distinct()
                )
            if limit is not None:
                query = query.limit(limit)
            clusters = db.scalars(query).all()
            rows = db.execute(
                select(Evidence.cluster_id, Evidence.id, Source.source_group_key)
                .join(Publication, Evidence.publication_id == Publication.id)
                .join(Source, Publication.source_id == Source.id)
                .where(Evidence.cluster_id.in_([cluster.id for cluster in clusters]))
            ).all()
            counts = Counter(cluster_id for cluster_id, _, _ in rows)
            groups: dict[int, set[str]] = {}
            for cluster_id, _, group in rows:
                if not group or not group.strip():
                    raise ValueError("Missing source_group_key; apply migration 0002 first")
                groups.setdefault(cluster_id, set()).add(group)
            return [
                ClusterItem(cluster.id, cluster.title, cluster.audience, cluster.description,
                            counts[cluster.id], frozenset(groups.get(cluster.id, set())))
                for cluster in clusters
            ]
        finally:
            db.rollback()


def cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denominator = sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    if not denominator:
        return float("inf")
    return max(0.0, 1 - sum(a * b for a, b in zip(left, right, strict=True)) / denominator)


def checked_vector(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if (len(vector) != settings.embedding_dimensions
            or not all(isfinite(value) for value in vector)
            or not any(vector)):
        raise ValueError("Invalid opportunity embedding dimensions/values")
    return vector


def representative(members: list[Member]) -> int:
    vectors = [member.item.embedding for member in members]
    centroid = tuple(sum(axis) / len(vectors) for axis in zip(*vectors, strict=True))
    if not any(centroid):
        raise ValueError("Cannot choose a cosine representative for a zero centroid")
    return min(
        members,
        key=lambda member: (cosine_distance(member.item.embedding, centroid), member.item.cluster.id),
    ).item.cluster.id


def source_groups(opportunity: VirtualOpportunity) -> set[str]:
    return {group for member in opportunity.members for group in member.item.cluster.source_groups}


def singleton(member: Member) -> VirtualOpportunity:
    return VirtualOpportunity(member.item.profile, member.item.embedding, [Member(member.item)])


def cache_key(cluster: ClusterItem) -> str:
    payload = {
        "version": CACHE_VERSION, "model": settings.openai_model,
        "embedding_model": settings.openai_embedding_model,
        "dimensions": settings.embedding_dimensions,
        "cluster": {"id": cluster.id, "title": cluster.title,
                    "audience": cluster.audience, "description": cluster.description},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def prepare_cluster(db, cluster: ClusterItem, cache: Path | None, call) -> ProfiledCluster:
    path = cache / f"{cache_key(cluster)}.json" if cache else None
    if path and path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            return ProfiledCluster(cluster, OpportunityProfile.model_validate(record["profile"]),
                                   checked_vector(record["embedding"]))
        except (ValueError, KeyError, TypeError):
            logger.warning("Invalid profile cache for Cluster %s; rebuilding", cluster.id)
    profile = call(build_cluster_opportunity_profile, db, cluster)
    vector = checked_vector(call(embed, db, embedding_input(profile)))
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "profile": profile.model_dump(), "embedding": vector,
            "model_name": settings.openai_model,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return ProfiledCluster(cluster, profile, vector)


def fit_opportunity(db, members: list[Member], stats: Counter, call,
                    current: VirtualOpportunity | None = None):
    """Remove explicit outliers, rebuild semantics, and revalidate until stable."""
    if len(members) < 2:
        raise ValueError("An Opportunity group needs at least two members")
    original = members
    profile = current.profile if current else None
    while len(members) >= 2:
        inputs = [member.item.ai_input() for member in members]
        if profile is None:
            profile = call(build_opportunity, db, inputs)
        stats["validation_calls"] += 1
        validation = call(validate_opportunity, db, profile, inputs)
        if validation.outlier_cluster_ids:
            members = [member for member in members
                       if member.item.cluster.id not in validation.outlier_cluster_ids]
            profile = None
            current = None
            continue
        if not validation.accepted:
            return None, original, validation
        vector = current.embedding if current else checked_vector(
            call(embed, db, embedding_input(profile)),
        )
        kept = {member.item.cluster.id for member in members}
        removed = [member for member in original if member.item.cluster.id not in kept]
        return VirtualOpportunity(profile, vector, members), removed, validation
    return None, original, validation


def simulate(clusters: list[ClusterItem], cache: Path | None = None, max_ai_usd: float = 2.0):
    opportunities: list[VirtualOpportunity] = []
    rejected: list[RejectedCandidate] = []
    cross_source_candidates: list[CandidateDiagnostic] = []
    stats = Counter(processed=len(clusters))
    with SessionLocal(autoflush=False) as db:
        # AiUsage stays pending in memory. READ ONLY also blocks accidental flush/commit writes.
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            spent = float(db.scalar(select(func.coalesce(func.sum(AiUsage.estimated_usd), 0))
                                    .where(AiUsage.created_at >= start)) or 0)
            allowance = min(max_ai_usd, settings.monthly_ai_budget_usd - spent)

            def call(function, *args):
                local_cost = sum(row.estimated_usd for row in db.new if isinstance(row, AiUsage))
                if local_cost >= allowance:
                    raise RuntimeError("Dry-run AI budget reached")
                return function(*args)

            for cluster in clusters:
                try:
                    item = prepare_cluster(db, cluster, cache, call)
                except Exception:
                    stats["profile_errors"] += 1
                    logger.exception("Profile unavailable for Cluster %s; skipped", cluster.id)
                    continue
                stats["profiles"] += 1
                # shortcut: O(n²) retrieval is for controlled runs; use pgvector for large corpora.
                candidates = sorted(
                    ((cosine_distance(item.embedding, opportunity.embedding), opportunity)
                     for opportunity in opportunities), key=lambda pair: pair[0],
                )[:settings.opportunity_candidate_top_k]
                candidates = [(distance, opportunity) for distance, opportunity in candidates
                              if distance < settings.opportunity_candidate_threshold]
                stats["pairs"] += len(candidates)
                candidate_records = []
                for distance, opportunity in candidates:
                    groups = frozenset(source_groups(opportunity))
                    diagnostic = CandidateDiagnostic(
                        cluster, tuple(member.item.cluster.id for member in opportunity.members),
                        opportunity.profile.underlying_pain, groups, distance,
                    )
                    # Symmetric difference means either side contributes an independent group.
                    if cluster.source_groups ^ groups:
                        stats["cross_source_pairs"] += 1
                        cross_source_candidates.append(diagnostic)
                    else:
                        stats["same_source_pairs"] += 1
                    candidate_records.append((opportunity, diagnostic))
                attached = False
                for opportunity, diagnostic in candidate_records:
                    distance = diagnostic.distance
                    ids = diagnostic.opportunity_cluster_ids
                    stats["calls"] += 1
                    try:
                        match = call(same_opportunity, db, item.profile, opportunity.profile)
                        diagnostic.match = match
                    except Exception as exc:
                        diagnostic.error = str(exc)
                        stats["errors"] += 1
                        rejected.append(RejectedCandidate(cluster.id, ids, distance, None,
                                                          str(exc), None, cluster.title,
                                                          opportunity.profile.underlying_pain))
                        logger.exception("same_opportunity failed for Cluster %s", cluster.id)
                        break
                    stats["true" if match.same_opportunity else "false"] += 1
                    if not match.accepted:
                        rejected.append(RejectedCandidate(cluster.id, ids, distance, match,
                                                          match.reason, match.too_broad_if_merged,
                                                          cluster.title, opportunity.profile.underlying_pain))
                        continue
                    incoming = Member(item, distance, match.confidence, match.reason)
                    try:
                        fitted, removed, validation = fit_opportunity(
                            db, [*opportunity.members, incoming], stats, call,
                        )
                    except Exception as exc:
                        stats["validation_errors"] += 1
                        rejected.append(RejectedCandidate(cluster.id, ids, distance, match,
                                                          str(exc), None, cluster.title,
                                                          opportunity.profile.underlying_pain))
                        logger.exception("Opportunity formation failed for Cluster %s", cluster.id)
                        break
                    if fitted is None or all(m.item.cluster.id != cluster.id for m in fitted.members):
                        rejected.append(RejectedCandidate(cluster.id, ids, distance, match,
                                                          validation.reason, validation.too_broad,
                                                          cluster.title, opportunity.profile.underlying_pain))
                        continue
                    opportunities[opportunities.index(opportunity)] = fitted
                    opportunities.extend(singleton(member) for member in removed)
                    stats["outliers_removed"] += len(removed)
                    attached = True
                    break
                if not attached:
                    opportunities.append(singleton(Member(item)))

            final: list[VirtualOpportunity] = []
            for opportunity in opportunities:
                if len(opportunity.members) < 2:
                    final.append(opportunity)
                    continue
                try:
                    fitted, removed, _ = fit_opportunity(
                        db, opportunity.members, stats, call, opportunity,
                    )
                except Exception:
                    stats["validation_errors"] += 1
                    logger.exception("Final Opportunity validation failed; splitting into singletons")
                    fitted, removed = None, opportunity.members
                final.extend(singleton(member) for member in removed)
                stats["outliers_removed"] += len(removed) if fitted else 0
                if fitted is None:
                    continue
                try:
                    fitted.representative_cluster_id = representative(fitted.members)
                    fitted.summary = call(
                        summarize_opportunity, db, fitted.profile,
                        [member.item.ai_input() for member in fitted.members],
                        fitted.representative_cluster_id,
                    )
                    if any(getattr(fitted.summary, field) != value
                           for field, value in fitted.profile.model_dump().items()):
                        fitted.summary = None
                        raise ValueError("Final summary changed validated Opportunity semantics")
                except Exception:
                    stats["summary_errors"] += 1
                    logger.exception("Final Opportunity summary unavailable")
                final.append(fitted)
            stats["estimated_ai_micro_usd"] = round(
                sum(row.estimated_usd for row in db.new if isinstance(row, AiUsage)) * 1_000_000,
            )
            return final, stats, rejected, cross_source_candidates
        finally:
            db.rollback()


def print_report(opportunities, stats, rejected, cross_source_candidates) -> None:
    merged = [opportunity for opportunity in opportunities if len(opportunity.members) >= 2]
    for label, value in (
        ("Clusters processed", stats["processed"]),
        ("Cluster opportunity profiles", stats["profiles"]),
        ("Profile errors (skipped clusters)", stats["profile_errors"]),
        ("Embedding candidate pairs", stats["pairs"]),
        ("Same-source candidate pairs", stats["same_source_pairs"]),
        ("Cross-source candidate pairs", stats["cross_source_pairs"]),
        ("same_opportunity calls", stats["calls"]),
        ("same_opportunity true", stats["true"]),
        ("same_opportunity false", stats["false"]),
        ("same_opportunity errors", stats["errors"]),
        ("Validation calls", stats["validation_calls"]),
        ("Validation errors", stats["validation_errors"]),
        ("Outlier clusters removed", stats["outliers_removed"]),
        ("Summary errors", stats["summary_errors"]),
        ("Virtual opportunities", len(opportunities)),
        ("Opportunities with >=2 clusters", len(merged)),
        ("Singletons", len(opportunities) - len(merged)),
        ("Opportunities with >=2 distinct source groups",
         sum(len(source_groups(opportunity)) >= 2 for opportunity in opportunities)),
        ("Opportunities with >=3 distinct source groups",
         sum(len(source_groups(opportunity)) >= 3 for opportunity in opportunities)),
        ("Largest opportunity", max((len(o.members) for o in opportunities), default=0)),
    ):
        print(f"{label}: {value}")
    print(f"Estimated AI cost (not persisted): ${stats['estimated_ai_micro_usd'] / 1_000_000:.6f}")
    for number, opportunity in enumerate(opportunities, 1):
        if len(opportunity.members) < 2:
            continue
        print(f"\n=== Opportunity {number} ===\n")
        print(f"Title:\n{opportunity.summary.title if opportunity.summary else '[summary unavailable]'}")
        for label, field_name in (
            ("Underlying pain", "underlying_pain"), ("JTBD", "job_to_be_done"),
            ("Desired outcome", "desired_outcome"), ("Audience", "audience"),
            ("Context", "context"), ("Scope", "scope"), ("Exclusions", "exclusions"),
        ):
            print(f"\n{label}:\n{getattr(opportunity.profile, field_name)}")
        if opportunity.summary:
            print(f"\nDescription:\n{opportunity.summary.description}")
        groups = sorted(source_groups(opportunity))
        print(f"\nRepresentative Cluster: {opportunity.representative_cluster_id}")
        print(f"Clusters: {len(opportunity.members)}")
        print(f"Evidence: {sum(m.item.cluster.evidence_count for m in opportunity.members)}")
        print(f"Distinct source groups: {len(groups)}")
        print("\nSource groups:")
        for group in groups:
            print(f"- {group}")
        for member in opportunity.members:
            cluster = member.item.cluster
            print(f"\n[Cluster {cluster.id}]\nproblem: {cluster.title}\naudience: {cluster.audience}")
            print(f"source groups: {', '.join(sorted(cluster.source_groups))}")
            print(f"embedding distance / match confidence: {member.distance} / {member.confidence}")
            print(f"reason: {member.reason}")

    print("\n=== CLOSEST REJECTED CANDIDATES ===")
    for candidate in sorted(rejected, key=lambda candidate: candidate.distance)[:20]:
        print(f"\nembedding distance: {candidate.distance:.4f}")
        print(f"Cluster A: {candidate.cluster_id}")
        print(f"problem: {candidate.cluster_problem}")
        print(f"Cluster B / Opportunity clusters: {list(candidate.opportunity_cluster_ids)}")
        print(f"opportunity underlying pain: {candidate.opportunity_pain}")
        print(f"same_opportunity confidence: {candidate.match.confidence if candidate.match else None}")
        print(f"reason: {candidate.reason}")
        print(f"too_broad_if_merged: {candidate.too_broad}")

    print("\n=== CLOSEST CROSS-SOURCE CANDIDATES ===")
    for candidate in sorted(cross_source_candidates, key=lambda candidate: candidate.distance)[:20]:
        print(f"\nembedding distance: {candidate.distance:.4f}")
        print(f"Cluster A id: {candidate.cluster.id}")
        print(f"Cluster A problem: {candidate.cluster.title}")
        print(f"Cluster A source groups: {', '.join(sorted(candidate.cluster.source_groups))}")
        print(f"Cluster B id / Opportunity clusters: {list(candidate.opportunity_cluster_ids)}")
        print(f"Opportunity underlying pain: {candidate.opportunity_pain}")
        print(f"Cluster B source groups: {', '.join(sorted(candidate.opportunity_source_groups))}")
        match = candidate.match
        result = match.same_opportunity if match else ("ERROR" if candidate.error else "NOT EVALUATED")
        print(f"same_opportunity result: {result}")
        print(f"confidence: {match.confidence if match else None}")
        reason = match.reason if match else candidate.error or "Earlier candidate accepted or failed"
        print(f"reason: {reason}")
        print(f"too_broad_if_merged: {match.too_broad_if_merged if match else None}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="first N selected clusters by ID")
    parser.add_argument("--source-groups", type=parse_source_groups, metavar="KEYS",
                        help="comma-separated source_group_key values; match any cluster evidence")
    parser.add_argument("--cache", type=Path, default=Path(".var/opportunity_profiles"))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--max-ai-usd", type=float, default=2.0,
                        help="local estimated AI budget; last call can exceed it (default: 2)")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not isfinite(args.max_ai_usd) or args.max_ai_usd <= 0:
        parser.error("--max-ai-usd must be finite and positive")
    if (settings.opportunity_candidate_top_k < 1
            or not 0 < settings.opportunity_candidate_threshold <= 2
            or not 0 <= settings.opportunity_match_confidence <= 1):
        parser.error("Invalid Opportunity candidate/match settings")
    logging.basicConfig(level=logging.INFO)
    print("READ-ONLY DRY RUN: no DB writes or Telegram publishing.")
    print("Source group filter: " + (", ".join(sorted(args.source_groups))
                                     if args.source_groups is not None else "ALL"))
    print(f"Candidate threshold={settings.opportunity_candidate_threshold}, "
          f"top_k={settings.opportunity_candidate_top_k}, "
          f"match confidence={settings.opportunity_match_confidence}")
    try:
        clusters = load_clusters(args.limit, args.source_groups)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Clusters selected: {len(clusters)}")
    opportunities, stats, rejected, cross_source_candidates = simulate(
        clusters, None if args.no_cache else args.cache, args.max_ai_usd,
    )
    print_report(opportunities, stats, rejected, cross_source_candidates)


if __name__ == "__main__":
    main()
