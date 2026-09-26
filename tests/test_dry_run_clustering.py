import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parents[1]))
dry_run = importlib.import_module("scripts.dry_run_clustering")


def item(evidence_id: int, source_id: int) -> dry_run.EvidenceItem:
    return dry_run.EvidenceItem(
        evidence_id,
        f"Problem {evidence_id}",
        "Audience",
        (1.0, 0.0),
        source_id,
    )


def test_simulate_merges_and_uses_safe_error_behavior(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(dry_run, "SessionLocal", MagicMock(return_value=db))
    monkeypatch.setattr(
        dry_run,
        "same_problem",
        MagicMock(side_effect=[True, RuntimeError("timeout")]),
    )

    clusters, stats = dry_run.simulate([item(1, 1), item(2, 2), item(3, 3)])

    assert [len(cluster.members) for cluster in clusters] == [2, 1]
    assert clusters[0].representative.id == 1
    assert stats == {"processed": 3, "calls": 2, "true": 1, "errors": 1}
