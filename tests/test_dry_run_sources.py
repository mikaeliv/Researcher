from unittest.mock import MagicMock, Mock

import pytest

from researcher.ai import Classification, Finding
from researcher.connectors.base import Item
from researcher.models import AiUsage, Source
from scripts import dry_run_sources


def item(item_id: str, title: str = "Messages keep getting lost") -> Item:
    return Item(
        external_id=item_id,
        url=f"https://example.com/{item_id}",
        title=title,
        text="I miss customer messages because they are spread across several applications.",
    )


def finding(
    classification: Classification = Classification.WORKFLOW_PAIN,
    product_solvable: bool = True,
) -> Finding:
    return Finding(
        contains_pain=True,
        classification=classification,
        product_solvable=product_solvable,
        problem="Customer messages get lost across applications",
        audience="Small businesses",
        workaround="Check each app manually",
        quote="messages",
        frequency="daily",
        loss=None,
        willingness_to_pay=False,
        confidence=0.9,
    )


def source() -> Source:
    return Source(kind="hackernews", name="Hacker News / Ask HN", config={})


def test_filtered_item_does_not_fetch_context_or_analyze(monkeypatch):
    context = Mock()
    analysis = Mock()
    monkeypatch.setattr(dry_run_sources, "fetch", Mock(return_value=[
        item("1", "Ask HN: Who is hiring? September 2026"),
    ]))
    monkeypatch.setattr(dry_run_sources, "fetch_context", context)
    monkeypatch.setattr(dry_run_sources, "analyze", analysis)

    stats = dry_run_sources.run_source(MagicMock(), source(), 10, emit=Mock())

    assert stats.filtered == 1
    assert stats.analyzed == 0
    context.assert_not_called()
    analysis.assert_not_called()


def test_pass_fetches_context_and_analyzes(monkeypatch):
    calls = []
    monkeypatch.setattr(dry_run_sources, "fetch", Mock(return_value=[item("1")]))

    def context(*_args):
        calls.append("context")
        return "Another user confirms the same problem."

    def analyze(*_args):
        calls.append("analyze")
        return finding()

    monkeypatch.setattr(dry_run_sources, "fetch_context", context)
    monkeypatch.setattr(dry_run_sources, "analyze", analyze)

    stats = dry_run_sources.run_source(MagicMock(), source(), 10, emit=Mock())

    assert calls == ["context", "analyze"]
    assert stats.analyzed == 1
    assert stats.accepted == 1


def test_accept_and_reject_use_production_gate(monkeypatch):
    monkeypatch.setattr(dry_run_sources, "fetch", Mock(return_value=[item("1"), item("2")]))
    monkeypatch.setattr(dry_run_sources, "fetch_context", Mock(return_value=""))
    monkeypatch.setattr(dry_run_sources, "analyze", Mock(side_effect=[
        finding(),
        finding(Classification.BUG_REPORT, product_solvable=False),
    ]))
    production_gate = dry_run_sources.accepts_as_evidence
    gate = Mock(wraps=production_gate)
    monkeypatch.setattr(dry_run_sources, "accepts_as_evidence", gate)

    stats = dry_run_sources.run_source(MagicMock(), source(), 10, emit=Mock())

    assert gate.call_count == 2
    assert stats.accepted == 1
    assert stats.rejected == 1


def test_item_error_does_not_stop_next_item(monkeypatch):
    monkeypatch.setattr(dry_run_sources, "fetch", Mock(return_value=[item("1"), item("2")]))
    monkeypatch.setattr(dry_run_sources, "fetch_context", Mock(return_value=""))
    monkeypatch.setattr(
        dry_run_sources,
        "analyze",
        Mock(side_effect=[RuntimeError("API error"), finding()]),
    )

    stats = dry_run_sources.run_source(MagicMock(), source(), 10, emit=Mock())

    assert stats.errors == 1
    assert stats.analyzed == 1
    assert stats.accepted == 1


def test_limit_caps_items_before_context_and_analysis(monkeypatch):
    context = Mock(return_value="")
    analysis = Mock(return_value=finding())
    monkeypatch.setattr(
        dry_run_sources,
        "fetch",
        Mock(return_value=[item("1"), item("2"), item("3")]),
    )
    monkeypatch.setattr(dry_run_sources, "fetch_context", context)
    monkeypatch.setattr(dry_run_sources, "analyze", analysis)

    stats = dry_run_sources.run_source(MagicMock(), source(), 2, emit=Mock())

    assert stats.fetched == 2
    assert context.call_count == 2
    assert analysis.call_count == 2


def test_main_selects_one_source(monkeypatch, capsys):
    selected = source()
    db = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = db
    load = Mock(return_value=([selected], []))
    run = Mock(return_value=dry_run_sources.Stats(selected.name))
    monkeypatch.setattr(dry_run_sources, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(dry_run_sources, "load_sources", load)
    monkeypatch.setattr(dry_run_sources, "run_source", run)

    dry_run_sources.main(["--source", selected.name, "--limit", "4"])

    load.assert_called_once_with(db, selected.name)
    run.assert_called_once_with(db, selected, 4)
    assert "=== TOTAL ===" in capsys.readouterr().out


def test_loaded_sources_are_detached_before_dry_run():
    selected = source()
    db = MagicMock()
    db.scalars.return_value.all.return_value = [selected]

    sources, missing = dry_run_sources.load_sources(db, selected.name)

    assert sources == [selected]
    assert missing == []
    db.expunge.assert_called_once_with(selected)


def test_only_ai_usage_is_added_to_session(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(dry_run_sources, "fetch", Mock(return_value=[item("1")]))
    monkeypatch.setattr(dry_run_sources, "fetch_context", Mock(return_value=""))

    def analyze(session, _text):
        session.add(AiUsage(model="test"))
        return finding()

    monkeypatch.setattr(dry_run_sources, "analyze", analyze)

    dry_run_sources.run_source(db, source(), 10, emit=Mock())

    assert db.add.call_count == 1
    assert isinstance(db.add.call_args.args[0], AiUsage)


def test_commit_guard_blocks_any_non_ai_usage_write():
    db = MagicMock()
    db.new = [object()]
    db.dirty = []
    db.deleted = []

    with pytest.raises(RuntimeError, match="non-AiUsage"):
        dry_run_sources._commit_ai_usage_only(db)

    db.rollback.assert_called_once()
    db.commit.assert_not_called()
