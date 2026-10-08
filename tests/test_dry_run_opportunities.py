"""Virtual grouping, source diversity, caching and rollback safety."""

import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from test_opportunities import profile

from researcher.opportunities import (
    OpportunityMatch,
    OpportunitySemantics,
    OpportunitySummary,
    OpportunityValidation,
)
from scripts import dry_run_opportunity_clustering as dry_run


def cluster(cluster_id, groups=("lemmy",)):
    return dry_run.ClusterItem(cluster_id, f"Concrete problem {cluster_id}", "operators",
                               "Host-specific differences", 2, frozenset(groups))


def match(**updates):
    return OpportunityMatch(**{
        "same_opportunity": True, "confidence": 0.9, "shared_underlying_pain": "Coordination",
        "shared_job_to_be_done": "Operate workloads", "reason": "coherent capability",
        "too_broad_if_merged": False, **updates,
    })


def validation(**updates):
    return OpportunityValidation(**{
        "valid": True, "confidence": 0.9, "too_broad": False,
        "outlier_cluster_ids": [], "reason": "every member fits", **updates,
    })


@pytest.fixture
def simulation(monkeypatch):
    db = MagicMock()
    db.__enter__.return_value = db
    db.scalar.return_value = 0
    db.new = []
    monkeypatch.setattr(dry_run, "SessionLocal", Mock(return_value=db))
    monkeypatch.setattr(dry_run.settings, "embedding_dimensions", 2)
    monkeypatch.setattr(dry_run, "build_cluster_opportunity_profile", Mock(return_value=profile()))
    monkeypatch.setattr(dry_run, "embed", Mock(return_value=[1.0, 0.0]))
    semantics = OpportunitySemantics(**profile().model_dump(), scope="Multi-host workloads",
                                    exclusions="Network setup; unrelated self-hosting jobs")
    monkeypatch.setattr(dry_run, "build_opportunity", Mock(return_value=semantics))
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(return_value=match()))
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(return_value=validation()))
    monkeypatch.setattr(dry_run, "summarize_opportunity", Mock(return_value=OpportunitySummary(
        **semantics.model_dump(), title="Operate workloads across hosts", description="Less manual work",
    )))
    return db, semantics


def assert_read_only(db):
    assert str(db.execute.call_args_list[0].args[0]) == "SET TRANSACTION READ ONLY"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.flush.assert_not_called()
    db.add.assert_not_called()


def test_positive_group_uses_updated_opportunity_and_one_final_summary(simulation, capsys):
    db, semantics = simulation
    groups, stats, rejected = dry_run.simulate([cluster(1), cluster(2, ("proxmox",)), cluster(3)])
    assert [[m.item.cluster.id for m in g.members] for g in groups] == [[1, 2, 3]]
    assert dry_run.same_opportunity.call_args_list[1].args[2] is semantics
    assert dry_run.summarize_opportunity.call_count == 1
    assert stats["profiles"] == 3
    assert stats["true"] == 2
    assert rejected == []
    assert dry_run.source_groups(groups[0]) == {"lemmy", "proxmox"}
    dry_run.print_report(groups, stats, rejected)
    output = capsys.readouterr().out
    assert "Distinct source groups: 2" in output
    assert "Evidence: 6" in output
    assert "=== CLOSEST REJECTED CANDIDATES ===" in output
    assert_read_only(db)


@pytest.mark.parametrize("result", [match(same_opportunity=False), match(confidence=0.69),
                                    match(too_broad_if_merged=True)])
def test_rejected_match_cannot_attach(simulation, monkeypatch, result):
    db, _ = simulation
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(return_value=result))
    groups, _, rejected = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert len(rejected) == 1
    dry_run.build_opportunity.assert_not_called()
    assert_read_only(db)


def test_semantic_chain_does_not_compare_only_to_last_member(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=[match(), match(
        same_opportunity=False, reason="Third cluster has a different job from the group",
    )]))
    groups, _, _ = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert [len(g.members) for g in groups] == [2, 1]
    assert isinstance(dry_run.same_opportunity.call_args_list[1].args[2], OpportunitySemantics)


def test_existing_outlier_is_removed_rebuilt_and_not_lost(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), validation(valid=False, outlier_cluster_ids=[1]), validation(), validation(),
    ]))
    groups, stats, _ = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert sorted(sorted(m.item.cluster.id for m in g.members) for g in groups) == [[1], [2, 3]]
    assert stats["outliers_removed"] == 1
    assert [row["cluster_id"] for row in dry_run.build_opportunity.call_args.args[1]] == [2, 3]


def test_new_outlier_preserves_existing_membership(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), validation(valid=False, outlier_cluster_ids=[3]), validation(), validation(),
    ]))
    groups, _, rejected = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert sorted(sorted(m.item.cluster.id for m in g.members) for g in groups) == [[1, 2], [3]]
    assert len(rejected) == 1


def test_broad_group_validation_vetoes_positive_match(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(return_value=validation(
        valid=False, too_broad=True,
    )))
    groups, _, rejected = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert rejected[0].too_broad is True


def test_match_errors_are_counted_and_rollback(simulation, monkeypatch):
    db, _ = simulation
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=RuntimeError("timeout")))
    groups, stats, rejected = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert stats["errors"] == 1
    assert rejected[0].reason == "timeout"
    assert_read_only(db)


def test_final_validation_failure_splits_group(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), RuntimeError("timeout"),
    ]))
    groups, stats, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert stats["validation_errors"] == 1
    dry_run.summarize_opportunity.assert_not_called()


def test_summary_cannot_broaden_validated_semantics(simulation, monkeypatch):
    _, semantics = simulation
    monkeypatch.setattr(dry_run, "summarize_opportunity", Mock(return_value=OpportunitySummary(
        **{**semantics.model_dump(), "underlying_pain": "Self-hosting is difficult"},
        title="Self-hosting", description="Too broad",
    )))
    groups, stats, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert groups[0].summary is None
    assert stats["summary_errors"] == 1


def test_representative_is_closest_to_centroid_not_first():
    vectors = [(1.0, 0.0), (0.8, 0.6), (0.6, 0.8)]
    members = [dry_run.Member(dry_run.ProfiledCluster(cluster(i), profile(), vector))
               for i, vector in enumerate(vectors, 1)]
    assert dry_run.representative(members) == 2


def test_source_group_diversity_is_platform_based():
    def opportunity(groups):
        members = [dry_run.Member(dry_run.ProfiledCluster(cluster(i, group), profile(), (1.0, 0.0)))
                   for i, group in enumerate(groups, 1)]
        return dry_run.VirtualOpportunity(profile(), (1.0, 0.0), members)
    assert len(dry_run.source_groups(opportunity([("lemmy",), ("lemmy",)]))) == 1
    assert len(dry_run.source_groups(opportunity([("lemmy",), ("proxmox",)]))) == 2


def test_cache_reuses_profiles_and_invalidates_changed_inputs(simulation, tmp_path):
    db, _ = simulation
    call = lambda function, *args: function(*args)
    original = cluster(1)
    cached = dry_run.prepare_cluster(db, original, tmp_path, call)
    assert dry_run.prepare_cluster(db, original, tmp_path, call) == cached
    assert dry_run.build_cluster_opportunity_profile.call_count == 1
    assert dry_run.embed.call_count == 1
    changed = dry_run.ClusterItem(1, "Changed problem", "operators", "new context", 3,
                                 frozenset({"lemmy"}))
    dry_run.prepare_cluster(db, changed, tmp_path, call)
    assert dry_run.build_cluster_opportunity_profile.call_count == 2
    path = tmp_path / f"{dry_run.cache_key(original)}.json"
    path.write_text('{"profile": {}, "embedding": [1]}')
    dry_run.prepare_cluster(db, original, tmp_path, call)
    assert dry_run.build_cluster_opportunity_profile.call_count == 3


def test_no_retrieval_at_threshold_and_top_k_is_respected(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(return_value=match(same_opportunity=False)))
    monkeypatch.setattr(dry_run, "cosine_distance", Mock(return_value=0.40))
    _, stats, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert stats["calls"] == 0
    monkeypatch.setattr(dry_run, "cosine_distance", Mock(return_value=0.39))
    _, stats, _ = dry_run.simulate([cluster(i) for i in range(1, 8)])
    assert stats["calls"] == sum(min(i, 5) for i in range(7))


def test_read_only_loader_counts_evidence_and_deduplicates_groups(monkeypatch):
    db = MagicMock()
    db.__enter__.return_value = db
    db.scalars.return_value.all.return_value = [cluster(1), cluster(2)]
    db.execute.return_value.all.return_value = [(1, 10, "lemmy"), (1, 11, "lemmy"), (2, 12, "proxmox")]
    monkeypatch.setattr(dry_run, "SessionLocal", Mock(return_value=db))
    items = dry_run.load_clusters(2)
    assert items[0].evidence_count == 2
    assert items[0].source_groups == frozenset({"lemmy"})
    assert "JOIN sources" in str(db.execute.call_args_list[1].args[0])
    assert_read_only(db)


def test_profile_error_is_reported_and_empty_input_is_safe(simulation, monkeypatch, capsys):
    monkeypatch.setattr(dry_run, "build_cluster_opportunity_profile", Mock(side_effect=RuntimeError()))
    groups, stats, rejected = dry_run.simulate([cluster(1)])
    assert groups == [] and rejected == []
    assert stats["processed"] == 1 and stats["profile_errors"] == 1
    dry_run.print_report([], Counter(), [])
    assert "Largest opportunity: 0" in capsys.readouterr().out
