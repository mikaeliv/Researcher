import html
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researcher.ai import analyze, embed, same_problem
from researcher.config import settings
from researcher.connectors.base import fetch
from researcher.models import Cluster, Evidence, Publication, Source, Stage

log = logging.getLogger(__name__)


def normalize(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[email]", value)
    return re.sub(r"\s+", " ", value).strip()


def useful(text: str) -> bool:
    return len(text) >= 55 and len(text.split()) >= 9


def ingest(db: Session, source: Source) -> int:
    items = fetch(source)
    count = 0
    # A source is only advanced after a successful fetch. Unique IDs protect replay.
    for item in items:
        normalized = normalize(item.title + "\n" + item.text)
        if not useful(normalized):
            continue
        if db.scalar(select(Publication.id).where(Publication.source_id == source.id,
                                                   Publication.external_id == item.external_id)):
            continue
        db.add(Publication(source_id=source.id, external_id=item.external_id, url=item.url,
                           title=item.title, raw_text=item.text, normalized_text=normalized,
                           published_at=item.published_at))
        count += 1
    source.last_success_at = datetime.now(UTC)
    source.last_error = None
    if items:
        dated = [int(i.published_at.timestamp()) for i in items if i.published_at]
        if dated and source.kind == "stackexchange":
            source.cursor = str(max(dated) + 1)
    db.commit()
    return count


def _attach_cluster(db: Session, evidence: Evidence, vector: list[float]) -> Cluster:
    distance = Cluster.embedding.cosine_distance(vector)
    candidates = db.execute(
        select(Cluster, distance.label("distance"))
        .where(Cluster.embedding.is_not(None), distance < settings.cluster_candidate_threshold)
        .order_by(distance)
        .limit(settings.cluster_candidate_top_k)
    ).all()
    for candidate, candidate_distance in candidates:
        if candidate_distance is None or candidate_distance >= settings.cluster_candidate_threshold:
            continue
        try:
            matches = same_problem(
                db,
                evidence.problem,
                evidence.audience,
                candidate.title,
                candidate.audience,
                candidate.description,
            )
        except Exception:
            log.exception("Semantic cluster verification failed; creating a new cluster")
            break
        if matches:
            candidate.last_seen_at = datetime.now(UTC)
            return candidate
    cluster = Cluster(title=evidence.problem, audience=evidence.audience,
                      description=evidence.problem, embedding=vector)
    db.add(cluster)
    db.flush()
    return cluster


def score_cluster(db: Session, cluster: Cluster) -> int:
    rows = db.execute(select(Evidence, Publication.source_id).join(Publication, Evidence.publication_id == Publication.id)
                      .where(Evidence.cluster_id == cluster.id)).all()
    count = len(rows)
    sources = len({source_id for _, source_id in rows})
    explicit_cost = any(e.loss for e, _ in rows)
    workaround = any(e.workaround for e, _ in rows)
    willing = any(e.willingness_to_pay for e, _ in rows)
    score = min(count * 12, 36) + min(sources * 10, 30)
    score += 15 if explicit_cost else 0
    score += 12 if workaround else 0
    score += 7 if willing else 0
    cluster.score = min(score, 100)
    return cluster.score


def process(db: Session, pub: Publication) -> None:
    finding = analyze(db, pub.normalized_text)
    if not finding.contains_pain or finding.confidence < 0.65:
        pub.stage = Stage.REJECTED
        db.commit()
        return
    vector = embed(db, finding.problem + " | " + finding.audience)
    evidence = Evidence(publication_id=pub.id, problem=finding.problem, audience=finding.audience,
                        workaround=finding.workaround, quote=finding.quote,
                        frequency=finding.frequency, loss=finding.loss,
                        willingness_to_pay=finding.willingness_to_pay,
                        confidence=finding.confidence, embedding=vector, model_name=settings.openai_model)
    db.add(evidence)
    db.flush()
    cluster = _attach_cluster(db, evidence, vector)
    evidence.cluster_id = cluster.id
    db.flush()
    score_cluster(db, cluster)
    pub.stage = Stage.ANALYZED
    db.commit()


def eligible(db: Session, cluster: Cluster) -> bool:
    if cluster.published_at or cluster.score < settings.publish_score:
        return False
    count, sources = db.execute(select(func.count(Evidence.id), func.count(func.distinct(Publication.source_id)))
                                .join(Publication, Evidence.publication_id == Publication.id)
                                .where(Evidence.cluster_id == cluster.id)).one()
    return count >= settings.min_cluster_evidence and sources >= settings.min_cluster_sources
