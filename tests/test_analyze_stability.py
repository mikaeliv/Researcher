"""Safeguard the diagnostic script against persisting analysis usage."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from researcher.ai import Classification
from scripts import analyze_stability


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
