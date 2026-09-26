from unittest.mock import MagicMock, Mock

from researcher import ai


def test_post_configures_optional_proxy(monkeypatch):
    monkeypatch.setattr(ai.settings, "openai_api_key", "test-key")

    for proxy, expected_kwargs in [
        ("", {"timeout": 60}),
        ("socks5://proxy:1080", {"timeout": 60, "proxy": "socks5://proxy:1080"}),
    ]:
        client = MagicMock()
        client.__enter__.return_value.post.return_value.json.return_value = {"ok": True}
        client_factory = Mock(return_value=client)
        monkeypatch.setattr(ai.settings, "openai_proxy_url", proxy)
        monkeypatch.setattr(ai.httpx, "Client", client_factory)

        assert ai._post("responses", {}) == {"ok": True}
        client_factory.assert_called_once_with(**expected_kwargs)


def test_same_problem_uses_structured_output_and_records_usage(monkeypatch):
    db = MagicMock()
    post = Mock(return_value={
        "output": [{"content": [{"type": "output_text", "text": '{"same_problem":true}'}]}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    })
    usage = Mock()
    monkeypatch.setattr(ai, "budget_available", Mock(return_value=True))
    monkeypatch.setattr(ai, "_post", post)
    monkeypatch.setattr(ai, "record_usage", usage)

    assert ai.same_problem(db, "problem", "audience", "title", "audience", "description")
    payload = post.call_args.args[1]
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    usage.assert_called_once()
