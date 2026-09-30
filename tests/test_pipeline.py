from unittest.mock import MagicMock

from researcher.ai import Classification, Finding
from researcher.models import Cluster, Evidence, Publication, Source, Stage
from researcher.pipeline import (
    _attach_cluster,
    accepts_as_evidence,
    likely_candidate,
    normalize,
    obvious_content_request,
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
    confidence: float = 0.9,
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
        confidence=confidence,
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


def test_obvious_content_requests_are_rejected():
    requests = (
        "What is the difference between an ETF and an index fund?",
        "Can someone explain why bond prices fall when interest rates rise?",
        "Can anyone explain the difference between a traditional IRA and a Roth IRA?",
        "Why do people buy expensive houses, cars, and smartphones on credit?",
        "Why would I ever not use instant wire transfer?",
        "What is the upshot of TWR calculation of an investment portfolio",
        "Will I be taxed if I bet at least $1 on a prediction market app?",
    )

    assert all(obvious_content_request(text) for text in requests)
    assert not any(likely_candidate(text) for text in requests)


def test_ambiguous_product_solvable_requests_pass():
    candidates = (
        (
            "Can anyone recommend a tool for sharing files with clients without giving them "
            "access to our whole drive?"
        ),
        (
            "Looking for a tool to automate invoice reconciliation because we spend hours doing "
            "it manually."
        ),
        (
            "How does everyone manage hundreds of self-hosted services without losing track of "
            "configuration?"
        ),
        (
            "There should be a service that alerts me when subscriptions silently increase "
            "their prices."
        ),
        "I spend several hours every week copying transactions between these two systems.",
        "How to invest long-term when moving countries frequently?",
        (
            "How do I budget effectively when income arrives at irregular intervals rather than "
            "a fixed monthly salary?"
        ),
        "What are my banking options as a minor that doesn't want to involve my parents?",
        (
            "How should I structure a monthly budget when my expenses vary significantly from "
            "month to month?"
        ),
    )

    assert not any(obvious_content_request(text) for text in candidates)
    assert all(likely_candidate(text) for text in candidates)


def test_existing_obvious_noise_is_rejected():
    noise = (
        "Ask HN: Who is hiring? (September 2026)",
        "This week's weekly roundup of interesting projects",
        "Product release notes for September 2026",
    )

    assert not any(likely_candidate(text) for text in noise)


def test_evidence_acceptance_is_centralized():
    for classification in (
        Classification.PRODUCT_OPPORTUNITY,
        Classification.SOLVED_PROBLEM,
        Classification.WORKFLOW_PAIN,
        Classification.SERVICE_GAP,
        Classification.FEATURE_REQUEST,
    ):
        assert accepts_as_evidence(finding(classification, product_solvable=True))
    assert accepts_as_evidence(
        finding(Classification.SOLVED_PROBLEM, product_solvable=True, confidence=0.65)
    )
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
