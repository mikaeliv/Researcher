import json
import sys
from unittest.mock import MagicMock, mock_open

from scripts import seed


def test_seed_can_disable_unlisted_sources(monkeypatch):
    old_source = MagicMock(enabled=True)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [old_source]
    db.scalar.return_value = None
    session = MagicMock()
    session.__enter__.return_value = db
    payload = [{
        "kind": "hackernews",
        "name": "Hacker News / Ask HN",
        "enabled": True,
        "config": {"limit": 10},
    }]
    monkeypatch.setattr(seed, "SessionLocal", MagicMock(return_value=session))
    open = mock_open(read_data=json.dumps(payload))
    monkeypatch.setattr("builtins.open", open)
    monkeypatch.setattr(sys, "argv", ["seed.py", "--disable-unlisted"])

    seed.main()

    open.assert_called_once_with(seed.SOURCES_PATH, encoding="utf-8")
    assert old_source.enabled is False
    created = db.add.call_args.args[0]
    assert created.name == "Hacker News / Ask HN"
    assert created.enabled is True
    assert created.source_group_key == "hackernews"
    db.commit.assert_called_once()
