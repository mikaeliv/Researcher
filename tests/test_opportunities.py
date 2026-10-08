"""AI contract and conservative Opportunity matching; no paid calls."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from researcher import opportunities as opportunity_ai
from researcher.config import Settings


def profile(**changes):
    return opportunity_ai.OpportunityProfile(**{
        "underlying_pain": "Manual coordination of workloads across hosts",
        "job_to_be_done": "Move and operate workloads across hosts consistently",
        "desired_outcome": "Treat several hosts as one manageable environment",
        "audience": "Small self-hosted virtualization operators",
        "context": "Multi-host virtualization infrastructure",
        **changes,
    })


def response(payload):
    return {"output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 2}}


@pytest.mark.parametrize(("problem", "other_job", "same", "broad", "reason"), [
    ("VM migration between hosts is difficult", "Deploy LXC apps across several hosts",
     True, False, "Both coordinate workloads across hosts"),
    ("Proxmox VM migration", "Configure Proxmox networking", False, False,
     "Shared Proxmox technology, different jobs"),
    ("Self-hosted calendar invites", "Self-hosted media transcoding", False, True,
     "Only 'self-hosting is difficult' explains both"),
    ("Recover backups after a disaster", "Retain backups for regulatory audit", False, False,
     "Materially different desired outcomes"),
])
def test_same_opportunity_structured_contract(monkeypatch, problem, other_job, same, broad, reason):
    match = {
        "same_opportunity": same, "confidence": 0.9,
        "shared_underlying_pain": "Manual coordination" if same else None,
        "shared_job_to_be_done": "Operate workloads" if same else None,
        "reason": reason, "too_broad_if_merged": broad,
    }
    post = Mock(return_value=response(match))
    usage = Mock()
    monkeypatch.setattr(opportunity_ai.ai, "budget_available", Mock(return_value=True))
    monkeypatch.setattr(opportunity_ai.ai, "_post", post)
    monkeypatch.setattr(opportunity_ai.ai, "record_usage", usage)
    result = opportunity_ai.same_opportunity(
        MagicMock(), profile(underlying_pain=problem), profile(job_to_be_done=other_job),
    )
    assert result.accepted is same
    assert result.too_broad_if_merged is broad
    payload = post.call_args.args[1]
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert "Shared technology" in payload["instructions"]
    assert "materially different desired outcomes" in payload["instructions"]
    assert "too_broad_if_merged=true" in payload["instructions"]
    assert other_job in payload["input"]
    usage.assert_called_once()


@pytest.mark.parametrize(("same", "confidence", "broad", "accepted"), [
    (True, 0.70, False, True), (True, 0.699, False, False),
    (False, 0.99, False, False), (True, 0.99, True, False),
])
def test_match_acceptance(same, confidence, broad, accepted):
    result = opportunity_ai.OpportunityMatch(
        same_opportunity=same, confidence=confidence, shared_underlying_pain=None,
        shared_job_to_be_done=None, reason="reason", too_broad_if_merged=broad,
    )
    assert result.accepted is accepted


def test_profile_abstraction_and_embedding_input(monkeypatch):
    post = Mock(return_value=response(profile().model_dump()))
    monkeypatch.setattr(opportunity_ai.ai, "budget_available", Mock(return_value=True))
    monkeypatch.setattr(opportunity_ai.ai, "_post", post)
    monkeypatch.setattr(opportunity_ai.ai, "record_usage", Mock())
    cluster = SimpleNamespace(id=1, title="VM migration", audience="operators",
                              description="Storage/config differs between hosts")
    result = opportunity_ai.build_cluster_opportunity_profile(MagicMock(), cluster)
    assert "Storage/config differs" in post.call_args.args[1]["input"]
    assert "Do not copy only a technology name" in post.call_args.args[1]["instructions"]
    text = opportunity_ai.embedding_input(result)
    assert text.startswith("Underlying pain:\nManual coordination")
    assert "\n\nJob to be done:\n" in text
    assert "\n\nDesired outcome:\n" in text
    assert "\n\nAudience:\n" in text
    assert "\n\nContext:\n" in text
    assert cluster.description not in text
    assert profile(underlying_pain="  Manual\n coordination  ").underlying_pain == "Manual coordination"
    with pytest.raises(ValidationError):
        profile(underlying_pain="   ")


def test_unknown_outliers_and_refusals_fail_closed(monkeypatch):
    post = Mock(return_value=response({
        "valid": False, "confidence": 0.95, "too_broad": False,
        "outlier_cluster_ids": [99], "reason": "different job",
    }))
    monkeypatch.setattr(opportunity_ai.ai, "budget_available", Mock(return_value=True))
    monkeypatch.setattr(opportunity_ai.ai, "_post", post)
    monkeypatch.setattr(opportunity_ai.ai, "record_usage", Mock())
    with pytest.raises(ValueError, match="unknown outlier"):
        opportunity_ai.validate_opportunity(MagicMock(), profile(), [{"cluster_id": 1}])
    post.return_value = {"output": [{"content": [{"type": "refusal", "refusal": "No"}]}]}
    with pytest.raises(RuntimeError, match="no output_text"):
        opportunity_ai.same_opportunity(MagicMock(), profile(), profile())


def test_opportunity_settings_do_not_change_strict_clustering():
    settings = Settings(_env_file=None)
    assert (settings.cluster_candidate_threshold, settings.cluster_candidate_top_k) == (0.35, 3)
    assert (settings.opportunity_candidate_threshold, settings.opportunity_candidate_top_k,
            settings.opportunity_match_confidence) == (0.40, 5, 0.70)
