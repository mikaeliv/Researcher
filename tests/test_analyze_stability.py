"""Safeguard the diagnostic script against persisting analysis usage."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from researcher.ai import Classification
from researcher.connectors.base import Item
from researcher.models import Source
from scripts import analyze_stability


def test_freeze_uses_fetched_items_and_context_without_database_writes(
    monkeypatch, tmp_path
) -> None:
    fixture = tmp_path / "corpus.json"
    monkeypatch.setattr(analyze_stability, "FIXTURE", fixture)
    sources = {
        name: Source(name=name, kind=kind, config={})
        for name, kind in (
            ("Stack Exchange / Personal Finance", "stackexchange"),
            ("Lemmy / Selfhosted", "lemmy"),
        )
    }
    db = MagicMock()
    db.scalar.side_effect = sources.values()
    session = MagicMock()
    session.__enter__.return_value = db
    monkeypatch.setattr(analyze_stability, "SessionLocal", Mock(return_value=session))

    def fetch(source):
        return [
            Item(target["external_id"], "https://example.com", target["title"], "body")
            for target in analyze_stability.TARGETS
            if target["source"] == source.name
        ]

    fetch_mock = Mock(side_effect=fetch)
    context_mock = Mock(side_effect=lambda _source, external_id: f"comment {external_id}")
    analyze_mock = Mock()
    monkeypatch.setattr(analyze_stability, "fetch", fetch_mock)
    monkeypatch.setattr(analyze_stability, "fetch_context", context_mock)
    monkeypatch.setattr(analyze_stability, "analyze", analyze_mock)

    analyze_stability.freeze()

    records = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(records) == 5
    assert fetch_mock.call_count == 2
    assert context_mock.call_count == 5
    assert records[2]["input"] == (
        "TITLE:\nJoplin sync\n\nORIGINAL AUTHOR:\nbody\n\n"
        "COMMENTS FROM OTHER USERS:\ncomment 52489909"
    )
    assert records[2]["context_chars"] == len("comment 52489909")
    assert records[2]["analyzed_chars"] == len(records[2]["input"])
    analyze_mock.assert_not_called()
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_each_analysis_session_rolls_back_even_when_a_call_fails(monkeypatch, tmp_path) -> None:
    fixture = tmp_path / "corpus.json"
    fixture.write_text(json.dumps([{
        "publication_id": 123,
        "title": "Joplin sync",
        "input": "frozen input",
    }]), encoding="utf-8")
    monkeypatch.setattr(analyze_stability, "FIXTURE", fixture)

    sessions = []

    def session_local():
        db = MagicMock()
        db.__enter__.return_value = db
        sessions.append(db)
        return db

    calls = []

    def analyze(db, text):
        calls.append((db, text))
        if len(calls) == 2:
            raise RuntimeError("OpenAI call failed")
        return SimpleNamespace(
            classification=Classification.CONTENT_REQUEST,
            product_solvable=False,
            contains_pain=False,
            confidence=0.9,
            problem="No product problem",
        )

    monkeypatch.setattr(analyze_stability, "SessionLocal", session_local)
    monkeypatch.setattr(analyze_stability, "analyze", analyze)

    with pytest.raises(RuntimeError, match="OpenAI call failed"):
        analyze_stability.experiment(2)

    assert [text for _, text in calls] == ["frozen input", "frozen input"]
    assert [db for db, _ in calls] == sessions
    for db in sessions:
        db.rollback.assert_called_once_with()
        db.commit.assert_not_called()
        db.__exit__.assert_called_once()
