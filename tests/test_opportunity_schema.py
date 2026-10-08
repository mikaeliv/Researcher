"""Independent sources, one-opportunity constraint, and reversible migration SQL."""

import importlib.util
import io
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from researcher.models import (
    Base,
    Cluster,
    Evidence,
    Opportunity,
    OpportunityCluster,
    Publication,
    Source,
)
from researcher.source_groups import source_group_key

ROOT = Path(__file__).parents[1]


def migration(filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "alembic/versions" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("kind", "name", "config", "expected"), [
    ("lemmy", "Lemmy / Selfhosted", {}, "lemmy"),
    ("lemmy", "Lemmy / Apps", {}, "lemmy"),
    ("rss", "Proxmox Support Forum", {}, "proxmox"),
    ("discourse", "TrueNAS Community", {}, "truenas"),
    ("discourse", "Nextcloud Community", {}, "nextcloud"),
    ("hackernews", "Hacker News / Ask HN", {}, "hackernews"),
    ("stackexchange", "Other Stack Exchange site", {}, "stackexchange"),
    ("rss", "Legacy forum category", {"url": "https://forum.proxmox.com/feed"}, "proxmox"),
    ("rss", "Unknown feed", {"url": "https://www.example.org/cat/1.rss"}, "example.org"),
])
def test_source_group_mapping(kind, name, config, expected):
    assert source_group_key(kind, name, config) == expected


def test_all_current_source_configs_have_correct_groups():
    for source in json.loads((ROOT / "sources.json").read_text()):
        assert source["source_group_key"] == source_group_key(
            source["kind"], source["name"], source["config"],
        )


def test_schema_membership_and_sql_distinct_source_groups():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sources = [Source(kind=kind, name=name, config={}) for kind, name in (
            ("lemmy", "Lemmy / Selfhosted"), ("lemmy", "Lemmy / Apps"),
            ("rss", "Proxmox Support Forum"),
        )]
        clusters = [Cluster(title=f"problem {i}") for i in range(3)]
        db.add_all([*sources, *clusters])
        db.flush()
        opportunity = Opportunity(
            title="Manage hosts", underlying_pain="Manual coordination",
            job_to_be_done="Operate across hosts", desired_outcome="One environment",
            audience="operators", context="multi-host", description="Shared pain",
            scope="Workload operations", exclusions="networking", representative_cluster_id=clusters[1].id,
        )
        db.add(opportunity)
        db.flush()
        for i, (source, cluster) in enumerate(zip(sources, clusters, strict=True)):
            publication = Publication(source_id=source.id, external_id=str(i), url="https://example.org",
                                      raw_text="text", normalized_text="text")
            db.add(publication)
            db.flush()
            db.add(Evidence(publication_id=publication.id, cluster_id=cluster.id, problem="pain",
                            quote="text", confidence=0.9, model_name="test"))
            db.add(OpportunityCluster(opportunity_id=opportunity.id, cluster_id=cluster.id,
                                      confidence=0.9, reason="shared job"))
        db.flush()
        query = (
            select(func.count(func.distinct(Source.source_group_key)))
            .select_from(OpportunityCluster)
            .join(Cluster, Cluster.id == OpportunityCluster.cluster_id)
            .join(Evidence, Evidence.cluster_id == Cluster.id)
            .join(Publication, Publication.id == Evidence.publication_id)
            .join(Source, Source.id == Publication.source_id)
            .where(OpportunityCluster.opportunity_id == opportunity.id)
        )
        assert db.scalar(query.where(Source.kind == "lemmy")) == 1
        assert db.scalar(query) == 2
        db.add(OpportunityCluster(opportunity_id=opportunity.id + 1, cluster_id=clusters[0].id,
                                  confidence=0.8, reason="second opportunity"))
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            db.flush()


def test_initial_revision_keeps_legacy_schema():
    initial = migration("0001_initial.py").initial_metadata()
    assert set(initial.tables) == {"sources", "publications", "clusters", "evidence", "ai_usage", "feedback"}
    assert "source_group_key" not in initial.tables["sources"].c
    for name in ("clusters", "evidence"):
        assert list(initial.tables[name].c.keys()) == list(Base.metadata.tables[name].c.keys())


def test_migration_upgrade_downgrade_sql_matches_models():
    module = migration("0002_opportunities.py")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": output,
    })
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    for table_name in ("opportunities", "opportunity_clusters", "cluster_opportunity_profiles"):
        assert f"CREATE TABLE {table_name}" in sql
        assert f"DROP TABLE {table_name}" in sql
        for column in Base.metadata.tables[table_name].columns:
            assert f"{column.name} " in sql
    assert "UNIQUE (cluster_id)" in sql
    assert "ADD COLUMN source_group_key" in sql
    assert "ALTER COLUMN source_group_key SET NOT NULL" in sql
    assert "DROP COLUMN source_group_key" in sql
    assert "UPDATE clusters" not in sql
    assert "UPDATE evidence" not in sql
    assert "vector(1536)" in sql.lower()


@pytest.mark.parametrize("budget", [2.0, 0.000001])
def test_real_ai_accounting_is_never_flushed_or_persisted(monkeypatch, budget):
    import sys
    from unittest.mock import Mock

    from sqlalchemy import event

    sys.path.insert(0, str(ROOT))
    from researcher import ai
    from researcher.opportunities import (
        OpportunityProfile,
        OpportunitySemantics,
        OpportunitySummary,
    )
    from scripts import dry_run_opportunity_clustering as dry_run

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    statements = []
    event.listen(engine, "before_cursor_execute",
                 lambda _conn, _cursor, statement, _params, _context, _many: statements.append(statement))

    class ReadOnlyTestSession(Session):
        def execute(self, statement, *args, **kwargs):
            if str(statement) == "SET TRANSACTION READ ONLY":
                return None  # SQLite has no PostgreSQL transaction command; checked separately.
            return super().execute(statement, *args, **kwargs)

    sessions = []

    def session_factory(**kwargs):
        db = ReadOnlyTestSession(engine, **kwargs)
        sessions.append(db)
        return db

    profile = OpportunityProfile(
        underlying_pain="Manual workload coordination", job_to_be_done="Operate workloads across hosts",
        desired_outcome="Manage one environment", audience="operators", context="Small multi-host setups",
    )
    semantics = OpportunitySemantics(**profile.model_dump(), scope="Workload operations",
                                    exclusions="Networking")
    payloads = {
        "OpportunityProfile": profile.model_dump(), "OpportunitySemantics": semantics.model_dump(),
        "OpportunitySummary": OpportunitySummary(**semantics.model_dump(), title="Manage workloads",
                                                  description="Reduce manual work").model_dump(),
        "OpportunityMatch": {
            "same_opportunity": True, "confidence": 0.9, "shared_underlying_pain": "Coordination",
            "shared_job_to_be_done": "Operate workloads", "reason": "shared job",
            "too_broad_if_merged": False,
        },
        "OpportunityValidation": {"valid": True, "confidence": 0.9, "too_broad": False,
                                  "outlier_cluster_ids": [], "reason": "Every member fits"},
    }

    def post(endpoint, payload):
        if endpoint == "embeddings":
            return {"data": [{"embedding": [1.0, 0.0]}], "usage": {"input_tokens": 10}}
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps(
            payloads[payload["text"]["format"]["name"]],
        )}]}], "usage": {"input_tokens": 10, "output_tokens": 2}}

    post_mock = Mock(side_effect=post)
    monkeypatch.setattr(dry_run, "SessionLocal", session_factory)
    monkeypatch.setattr(dry_run.settings, "embedding_dimensions", 2)
    monkeypatch.setattr(ai, "_post", post_mock)
    clusters = [dry_run.ClusterItem(i, f"Problem {i}", "operators", "context", 1,
                                    frozenset({"lemmy"})) for i in (1, 2)]
    groups, stats, _, _ = dry_run.simulate(clusters, max_ai_usd=budget)
    if budget == 2.0:
        assert len(groups) == 1 and len(groups[0].members) == 2
        assert stats["estimated_ai_micro_usd"] > 0
    else:
        assert groups == []
        assert post_mock.call_count == 1
        assert stats["profile_errors"] == 2
    assert all(not statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
               for statement in statements)
    with Session(engine) as db:
        for model in (Opportunity, OpportunityCluster, ai.AiUsage, Cluster, Evidence):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    assert all(not db.new for db in sessions)
