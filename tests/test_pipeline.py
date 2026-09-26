from unittest.mock import MagicMock

from researcher.ai import Classification, Finding
from researcher.models import Cluster, Evidence, Publication, Source, Stage
from researcher.pipeline import (
    _attach_cluster,
    accepts_as_evidence,
    likely_candidate,
    normalize,
    process,
    useful,
)


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


def finding(
    classification: Classification,
    product_solvable: bool,
    contains_pain: bool = True,
) -> Finding:
    return Finding(
        contains_pain=contains_pain,
        classification=classification,
        product_solvable=product_solvable,
        problem="A recurring problem",
        audience="People",
        workaround=None,
        quote="problem",
        frequency=None,
        loss=None,
        willingness_to_pay=False,
        confidence=0.9,
    )


def test_candidate_filter_favors_recall():
    assert likely_candidate(
        "My client messages are spread across Slack, WhatsApp and email and I keep missing them."
    )
    assert likely_candidate(
        "Google Sign-In throws ApiException 10 after installing from Play Store"
    )
    assert not likely_candidate("Ask HN: Who is hiring? (September 2026)")
    assert not likely_candidate("Help")


def test_evidence_acceptance_is_centralized():
    for classification in (
        Classification.PRODUCT_OPPORTUNITY,
        Classification.WORKFLOW_PAIN,
        Classification.SERVICE_GAP,
        Classification.FEATURE_REQUEST,
    ):
        assert accepts_as_evidence(finding(classification, product_solvable=True))
    assert not accepts_as_evidence(
        finding(Classification.FEATURE_REQUEST, product_solvable=False)
    )
    for classification in (
        Classification.TECH_SUPPORT,
        Classification.BUG_REPORT,
        Classification.CONTENT_REQUEST,
        Classification.OTHER,
    ):
        assert not accepts_as_evidence(finding(classification, product_solvable=False))


def test_process_filters_before_fetching_context_or_calling_llm(monkeypatch):
    db = MagicMock()
    source = Source(kind="hackernews", name="HN", config={})
    pub = Publication(
        source=source,
        external_id="1",
        url="https://example.com/1",
        title="Who is hiring?",
        raw_text="ORIGINAL AUTHOR:\nSeptember jobs",
        normalized_text="Ask HN: Who is hiring? September jobs",
    )
    context = MagicMock()
    analysis = MagicMock()
    monkeypatch.setattr("researcher.pipeline.fetch_context", context)
    monkeypatch.setattr("researcher.pipeline.analyze", analysis)

    process(db, pub)

    assert pub.stage == Stage.FILTERED
    context.assert_not_called()
    analysis.assert_not_called()
    db.commit.assert_called_once()


def test_process_fetches_context_only_after_candidate_filter(monkeypatch):
    db = MagicMock()
    source = Source(kind="lemmy", name="Lemmy", config={})
    pub = Publication(
        source=source,
        external_id="2",
        url="https://example.com/2",
        title="Messages keep getting lost",
        raw_text="ORIGINAL AUTHOR:\nI miss customer messages across several apps.",
        normalized_text="Messages keep getting lost I miss customer messages across several apps.",
    )
    calls = []

    def context(*_args):
        calls.append("context")
        return "[comment] This happens to me too."

    def analyze(_db, text):
        calls.append("analyze")
        assert "COMMENTS FROM OTHER USERS" in text
        return finding(Classification.OTHER, product_solvable=False)

    monkeypatch.setattr("researcher.pipeline.fetch_context", context)
    monkeypatch.setattr("researcher.pipeline.analyze", analyze)

    process(db, pub)

    assert calls == ["context", "analyze"]
    assert pub.stage == Stage.REJECTED


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
