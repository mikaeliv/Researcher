import html
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researcher.ai import Classification, Finding, analyze, embed, same_problem
from researcher.config import settings
from researcher.connectors.base import Item, fetch, fetch_context
from researcher.models import Cluster, Evidence, Publication, Source, Stage

log = logging.getLogger(__name__)


def normalize(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[email]", value)
    return re.sub(r"\s+", " ", value).strip()


def useful(text: str) -> bool:
    return len(text) >= 20 and len(text.split()) >= 4


def likely_candidate(text: str) -> bool:
    """Reject only obvious pre-LLM noise; ambiguous publications deliberately pass."""
    if not useful(text):
        return False
    lowered = text.casefold()
    obvious_noise = (
        "who is hiring",
        "who wants to be hired",
        "freelancer? seeking freelancer",
        "weekly roundup",
        "monthly roundup",
        "release notes",
        "changelog",
    )
    return not any(marker in lowered for marker in obvious_noise)


def item_raw_text(item: Item) -> str:
    sections = ["ORIGINAL AUTHOR:\n" + item.text.strip()]
    if item.metadata:
        metadata = "\n".join(f"{key}: {value}" for key, value in item.metadata.items())
        sections.append("METADATA:\n" + metadata)
    return "\n\n".join(sections)


def add_context(raw_text: str, context: str) -> str:
    if not context:
        return raw_text
    return raw_text + "\n\nCOMMENTS FROM OTHER USERS:\n" + context


def analysis_input(title: str, raw_text: str) -> str:
    return f"TITLE:\n{title}\n\n{raw_text}"


def accepts_as_evidence(finding: Finding) -> bool:
    accepted = {
        Classification.PRODUCT_OPPORTUNITY,
        Classification.FEATURE_REQUEST,
        Classification.WORKFLOW_PAIN,
        Classification.SERVICE_GAP,
    }
    return (
        finding.contains_pain
        and finding.product_solvable
        and finding.classification in accepted
        and finding.confidence >= 0.65
    )


def ingest(db: Session, source: Source) -> int:
    items = fetch(source)
    count = 0
    # A source is only advanced after a successful fetch. Unique IDs protect replay.
    for item in items:
        raw_text = item_raw_text(item)
        normalized = normalize(item.title + "\n" + raw_text)
        if not normalized:
            continue
        if db.scalar(select(Publication.id).where(Publication.source_id == source.id,
                                                   Publication.external_id == item.external_id)):
            continue
        db.add(Publication(source_id=source.id, external_id=item.external_id, url=item.url,
                           title=item.title, raw_text=raw_text, normalized_text=normalized,
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
    if not likely_candidate(pub.normalized_text):
        pub.stage = Stage.FILTERED
        db.commit()
        return
    context = fetch_context(pub.source, pub.external_id)
    pub.raw_text = add_context(pub.raw_text, context)
    if context:
        pub.normalized_text = normalize(pub.title + "\n" + pub.raw_text)
    finding = analyze(db, analysis_input(pub.title, pub.raw_text))
    if not accepts_as_evidence(finding):
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
