from unittest.mock import MagicMock

from researcher.ai import Finding
from researcher.models import Cluster, Evidence
from researcher.pipeline import _attach_cluster, normalize, useful


def test_normalize_removes_email_and_markup():
    assert normalize("<b>Help</b>  me@example.com") == "Help [email]"


def test_short_text_is_rejected():
    assert not useful("Help")
    assert useful("I have to copy each customer request manually into a spreadsheet every single day")


def test_confidence_is_bounded():
    try:
        Finding(contains_pain=True, confidence=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("Confidence above one must be rejected")


def evidence(audience: str = "Android developers") -> Evidence:
    return Evidence(problem="Build fails with a locked Gradle journal", audience=audience)


def cluster(cluster_id: int, audience: str = "Mobile developers") -> Cluster:
    return Cluster(id=cluster_id, title=f"Problem {cluster_id}", audience=audience,
                   description=f"Description {cluster_id}", embedding=[0.0] * 1536)


def test_attach_cluster_without_candidates_skips_verification(monkeypatch):
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    verify = MagicMock()
    monkeypatch.setattr("researcher.pipeline.same_problem", verify)

    result = _attach_cluster(db, evidence(), [0.1] * 1536)

    verify.assert_not_called()
    db.add.assert_called_once_with(result)
    db.flush.assert_called_once()


def test_attach_cluster_stops_after_first_match_despite_different_audience_strings(monkeypatch):
    db = MagicMock()
    first, second = cluster(1), cluster(2)
    db.execute.return_value.all.return_value = [(first, 0.2), (second, 0.25)]
    verify = MagicMock(return_value=True)
    monkeypatch.setattr("researcher.pipeline.same_problem", verify)

    result = _attach_cluster(db, evidence("Developers using Android Studio"), [0.1] * 1536)

    assert result is first
    assert first.last_seen_at is not None
    assert verify.call_count == 1
    db.add.assert_not_called()


def test_attach_cluster_uses_second_match(monkeypatch):
    db = MagicMock()
    first, second = cluster(1), cluster(2)
    db.execute.return_value.all.return_value = [(first, 0.2), (second, 0.25)]
    verify = MagicMock(side_effect=[False, True])
    monkeypatch.setattr("researcher.pipeline.same_problem", verify)

    result = _attach_cluster(db, evidence(), [0.1] * 1536)

    assert result is second
    assert verify.call_count == 2
    db.add.assert_not_called()


def test_attach_cluster_creates_cluster_when_all_candidates_differ(monkeypatch):
    db = MagicMock()
    db.execute.return_value.all.return_value = [(cluster(1), 0.2), (cluster(2), 0.25)]
    monkeypatch.setattr("researcher.pipeline.same_problem", MagicMock(return_value=False))

    result = _attach_cluster(db, evidence(), [0.1] * 1536)

    assert result.title == "Build fails with a locked Gradle journal"
    db.add.assert_called_once_with(result)
    db.flush.assert_called_once()


def test_attach_cluster_ignores_candidates_outside_threshold(monkeypatch):
    db = MagicMock()
    db.execute.return_value.all.return_value = [(cluster(1), 0.35)]
    verify = MagicMock()
    monkeypatch.setattr("researcher.pipeline.same_problem", verify)

    result = _attach_cluster(db, evidence(), [0.1] * 1536)

    verify.assert_not_called()
    db.add.assert_called_once_with(result)


def test_attach_cluster_verification_error_creates_cluster(monkeypatch):
    db = MagicMock()
    db.execute.return_value.all.return_value = [(cluster(1), 0.2)]
    monkeypatch.setattr(
        "researcher.pipeline.same_problem", MagicMock(side_effect=RuntimeError("API timeout"))
    )

    result = _attach_cluster(db, evidence(), [0.1] * 1536)

    assert result.title == "Build fails with a locked Gradle journal"
    db.add.assert_called_once_with(result)
