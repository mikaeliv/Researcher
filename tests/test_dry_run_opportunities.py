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
    groups, stats, rejected, cross = dry_run.simulate([cluster(1), cluster(2, ("proxmox",)), cluster(3)])
    assert [[m.item.cluster.id for m in g.members] for g in groups] == [[1, 2, 3]]
    assert dry_run.same_opportunity.call_args_list[1].args[2] is semantics
    assert dry_run.summarize_opportunity.call_count == 1
    assert stats["profiles"] == 3
    assert stats["true"] == 2
    assert rejected == []
    assert dry_run.source_groups(groups[0]) == {"lemmy", "proxmox"}
    dry_run.print_report(groups, stats, rejected, cross)
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
    groups, _, rejected, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert len(rejected) == 1
    dry_run.build_opportunity.assert_not_called()
    assert_read_only(db)


def test_semantic_chain_does_not_compare_only_to_last_member(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=[match(), match(
        same_opportunity=False, reason="Third cluster has a different job from the group",
    )]))
    groups, _, _, _ = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert [len(g.members) for g in groups] == [2, 1]
    assert isinstance(dry_run.same_opportunity.call_args_list[1].args[2], OpportunitySemantics)


def test_existing_outlier_is_removed_rebuilt_and_not_lost(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), validation(valid=False, outlier_cluster_ids=[1]), validation(), validation(),
    ]))
    groups, stats, _, _ = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert sorted(sorted(m.item.cluster.id for m in g.members) for g in groups) == [[1], [2, 3]]
    assert stats["outliers_removed"] == 1
    assert [row["cluster_id"] for row in dry_run.build_opportunity.call_args.args[1]] == [2, 3]


def test_new_outlier_preserves_existing_membership(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), validation(valid=False, outlier_cluster_ids=[3]), validation(), validation(),
    ]))
    groups, _, rejected, _ = dry_run.simulate([cluster(1), cluster(2), cluster(3)])
    assert sorted(sorted(m.item.cluster.id for m in g.members) for g in groups) == [[1, 2], [3]]
    assert len(rejected) == 1


def test_broad_group_validation_vetoes_positive_match(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(return_value=validation(
        valid=False, too_broad=True,
    )))
    groups, _, rejected, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert rejected[0].too_broad is True


def test_match_errors_are_counted_and_rollback(simulation, monkeypatch):
    db, _ = simulation
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=RuntimeError("timeout")))
    groups, stats, rejected, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert stats["errors"] == 1
    assert rejected[0].reason == "timeout"
    assert_read_only(db)


def test_final_validation_failure_splits_group(simulation, monkeypatch):
    monkeypatch.setattr(dry_run, "validate_opportunity", Mock(side_effect=[
        validation(), RuntimeError("timeout"),
    ]))
    groups, stats, _, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert [len(g.members) for g in groups] == [1, 1]
    assert stats["validation_errors"] == 1
    dry_run.summarize_opportunity.assert_not_called()


def test_summary_cannot_broaden_validated_semantics(simulation, monkeypatch):
    _, semantics = simulation
    monkeypatch.setattr(dry_run, "summarize_opportunity", Mock(return_value=OpportunitySummary(
        **{**semantics.model_dump(), "underlying_pain": "Self-hosting is difficult"},
        title="Self-hosting", description="Too broad",
    )))
    groups, stats, _, _ = dry_run.simulate([cluster(1), cluster(2)])
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
    _, stats, _, _ = dry_run.simulate([cluster(1), cluster(2)])
    assert stats["calls"] == 0
    monkeypatch.setattr(dry_run, "cosine_distance", Mock(return_value=0.39))
    _, stats, _, _ = dry_run.simulate([cluster(i) for i in range(1, 8)])
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
    groups, stats, rejected, _ = dry_run.simulate([cluster(1)])
    assert groups == [] and rejected == []
    assert stats["processed"] == 1 and stats["profile_errors"] == 1
    dry_run.print_report([], Counter(), [], [])
    assert "Largest opportunity: 0" in capsys.readouterr().out


@pytest.fixture
def cluster_database(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from researcher.models import Base, Cluster, Evidence, Publication, Source

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sources = {group: Source(kind="rss", name=group, source_group_key=group, config={})
                   for group in ("dev.to", "lemmy", "proxmox")}
        db.add_all(sources.values())
        db.flush()
        # Cluster 3 has repeated selected evidence; Cluster 5 matches only its second evidence.
        for cluster_id, groups in enumerate((
            ("dev.to",), ("lemmy",), ("lemmy", "lemmy"), ("lemmy",), ("dev.to", "proxmox"),
        ), 1):
            db.add(Cluster(id=cluster_id, title=f"Problem {cluster_id}", description="context"))
            db.flush()
            for index, group in enumerate(groups):
                publication = Publication(source_id=sources[group].id,
                                          external_id=f"{cluster_id}-{index}", url="https://example.org",
                                          raw_text="text", normalized_text="text")
                db.add(publication)
                db.flush()
                db.add(Evidence(publication_id=publication.id, cluster_id=cluster_id, problem="pain",
                                quote="text", confidence=0.9, model_name="test"))
        db.commit()

    sessions = []

    class ReadOnlyTestSession(Session):
        def execute(self, statement, *args, **kwargs):
            if str(statement) == "SET TRANSACTION READ ONLY":
                return None
            return super().execute(statement, *args, **kwargs)

    def session_factory(**kwargs):
        db = ReadOnlyTestSession(engine, **kwargs)
        db.rollback = Mock(wraps=db.rollback)
        db.commit = Mock(wraps=db.commit)
        sessions.append(db)
        return db

    monkeypatch.setattr(dry_run, "SessionLocal", session_factory)
    return sessions


def test_source_filter_selects_unique_clusters_and_preserves_all_evidence(cluster_database):
    selected = dry_run.load_clusters(requested_source_groups={"lemmy"})
    assert [item.id for item in selected] == [2, 3, 4]
    assert selected[1].evidence_count == 2
    mixed = dry_run.load_clusters(requested_source_groups={"proxmox"})
    assert [item.id for item in mixed] == [5]
    assert mixed[0].evidence_count == 2
    assert mixed[0].source_groups == frozenset({"dev.to", "proxmox"})
    for db in cluster_database:
        db.rollback.assert_called()
        db.commit.assert_not_called()


def test_limit_applies_after_filter_and_unfiltered_behavior_is_unchanged(cluster_database):
    assert [item.id for item in dry_run.load_clusters(2, {"lemmy"})] == [2, 3]
    assert [item.id for item in dry_run.load_clusters(2)] == [1, 2]
    assert [item.id for item in dry_run.load_clusters()] == [1, 2, 3, 4, 5]


def test_unknown_group_fails_before_processing(cluster_database):
    with pytest.raises(ValueError, match="Unknown source_group_key: nextcloud, unknown"):
        dry_run.load_clusters(requested_source_groups={"lemmy", "nextcloud", "unknown"})
    cluster_database[0].commit.assert_not_called()
    cluster_database[0].rollback.assert_called()


def test_source_group_parser_trims_and_deduplicates():
    assert dry_run.parse_source_groups("lemmy, truenas ,nextcloud,lemmy") == {
        "lemmy", "truenas", "nextcloud",
    }


@pytest.mark.parametrize("value", ["", "  ", ", ,", "lemmy,,proxmox", "lemmy,"])
def test_empty_source_group_keys_are_cli_errors(monkeypatch, capsys, value):
    load = Mock()
    process = Mock()
    monkeypatch.setattr(dry_run, "load_clusters", load)
    monkeypatch.setattr(dry_run, "simulate", process)
    monkeypatch.setattr(sys, "argv", ["dry-run", "--source-groups", value])
    with pytest.raises(SystemExit) as error:
        dry_run.main()
    assert error.value.code == 2
    assert "nonempty comma-separated keys" in capsys.readouterr().err
    load.assert_not_called()
    process.assert_not_called()


def test_unknown_source_group_is_cli_error(monkeypatch, capsys):
    monkeypatch.setattr(dry_run, "load_clusters", Mock(side_effect=ValueError(
        "Unknown source_group_key: unknown",
    )))
    process = Mock()
    monkeypatch.setattr(dry_run, "simulate", process)
    monkeypatch.setattr(sys, "argv", ["dry-run", "--source-groups", "unknown"])
    with pytest.raises(SystemExit) as error:
        dry_run.main()
    assert error.value.code == 2
    assert "Unknown source_group_key: unknown" in capsys.readouterr().err
    process.assert_not_called()


@pytest.mark.parametrize(("argument", "expected"), [
    (None, "ALL"), (" truenas,lemmy,proxmox,nextcloud ", "lemmy, nextcloud, proxmox, truenas"),
])
def test_cli_prints_selection_before_ai(monkeypatch, capsys, argument, expected):
    selected = [cluster(1)]
    load = Mock(return_value=selected)
    monkeypatch.setattr(dry_run, "load_clusters", load)

    def process(*args):
        assert args[0] == selected
        output = capsys.readouterr().out
        assert f"Source group filter: {expected}" in output
        assert "Clusters selected: 1" in output
        return [], Counter(), [], []

    monkeypatch.setattr(dry_run, "simulate", process)
    argv = ["dry-run", "--limit", "2"]
    if argument is not None:
        argv += ["--source-groups", argument]
    monkeypatch.setattr(sys, "argv", argv)
    dry_run.main()
    load.assert_called_once_with(2, None if argument is None else {
        "lemmy", "truenas", "proxmox", "nextcloud",
    })


@pytest.mark.parametrize(("left", "right", "cross"), [
    (("lemmy",), ("lemmy",), False),
    (("lemmy",), ("proxmox",), True),
    (("lemmy", "proxmox"), ("proxmox", "lemmy"), False),
    (("lemmy",), ("lemmy", "proxmox"), True),
    (("lemmy", "nextcloud"), ("lemmy", "truenas"), True),
])
def test_candidate_source_counts_use_symmetric_difference(simulation, monkeypatch, left, right, cross):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(return_value=match(same_opportunity=False)))
    _, stats, _, diagnostics = dry_run.simulate([cluster(1, left), cluster(2, right)])
    assert stats["pairs"] == 1
    assert stats["cross_source_pairs"] == int(cross)
    assert stats["same_source_pairs"] == int(not cross)
    assert len(diagnostics) == int(cross)


def test_cross_source_candidates_include_accepted_groups_and_snapshot_all_member_sources(
    simulation, capsys,
):
    groups, stats, rejected, diagnostics = dry_run.simulate([
        cluster(1, ("lemmy",)), cluster(2, ("proxmox",)), cluster(3, ("lemmy",)),
    ])
    assert stats["pairs"] == stats["same_source_pairs"] + stats["cross_source_pairs"] == 2
    assert rejected == []
    assert len(diagnostics) == 2
    assert diagnostics[0].opportunity_cluster_ids == (1,)
    assert diagnostics[0].opportunity_source_groups == frozenset({"lemmy"})
    assert diagnostics[1].opportunity_cluster_ids == (1, 2)
    assert diagnostics[1].opportunity_source_groups == frozenset({"lemmy", "proxmox"})
    assert all(candidate.match.same_opportunity for candidate in diagnostics)
    dry_run.print_report(groups, stats, rejected, diagnostics)
    output = capsys.readouterr().out
    assert "Same-source candidate pairs: 0" in output
    assert "Cross-source candidate pairs: 2" in output
    block = output.split("=== CLOSEST CROSS-SOURCE CANDIDATES ===")[1]
    for text in ("Cluster A id: 2", "Cluster A problem: Concrete problem 2",
                 "Cluster A source groups: proxmox", "Opportunity underlying pain:",
                 "Cluster B source groups: lemmy, proxmox", "same_opportunity result: True",
                 "confidence: 0.9", "reason: coherent capability", "too_broad_if_merged: False"):
        assert text in block


def test_retrieved_cross_source_candidates_not_checked_after_attach_are_still_reported(
    simulation, monkeypatch,
):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=[
        match(same_opportunity=False), match(),
    ]))
    _, stats, _, diagnostics = dry_run.simulate([
        cluster(1, ("lemmy",)), cluster(2, ("proxmox",)), cluster(3, ("nextcloud",)),
    ])
    assert stats["cross_source_pairs"] == 3
    assert stats["calls"] == 2
    assert len(diagnostics) == 3
    assert diagnostics[-1].match is None and diagnostics[-1].error is None


def test_cross_source_api_errors_are_reported(simulation, monkeypatch, capsys):
    monkeypatch.setattr(dry_run, "same_opportunity", Mock(side_effect=RuntimeError("timeout")))
    groups, stats, rejected, diagnostics = dry_run.simulate([
        cluster(1, ("lemmy",)), cluster(2, ("proxmox",)),
    ])
    assert stats["cross_source_pairs"] == 1
    assert diagnostics[0].error == "timeout"
    dry_run.print_report(groups, stats, rejected, diagnostics)
    block = capsys.readouterr().out.split("=== CLOSEST CROSS-SOURCE CANDIDATES ===")[1]
    assert "same_opportunity result: ERROR" in block
    assert "reason: timeout" in block


def test_closest_cross_source_diagnostics_are_sorted_and_limited_to_twenty(capsys):
    diagnostics = [dry_run.CandidateDiagnostic(
        cluster(i, ("proxmox",)), (100,), "Pain", frozenset({"lemmy"}), i / 100,
    ) for i in range(25, 0, -1)]
    dry_run.print_report([], Counter(), [], diagnostics)
    block = capsys.readouterr().out.split("=== CLOSEST CROSS-SOURCE CANDIDATES ===")[1]
    assert block.count("embedding distance:") == 20
    assert block.index("embedding distance: 0.0100") < block.index("embedding distance: 0.2000")
    assert "embedding distance: 0.2100" not in block
    assert "same_opportunity result: NOT EVALUATED" in block
