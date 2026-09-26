from datetime import UTC, datetime
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researcher.config import settings
from researcher.models import AiUsage


class Classification(StrEnum):
    PRODUCT_OPPORTUNITY = "PRODUCT_OPPORTUNITY"
    FEATURE_REQUEST = "FEATURE_REQUEST"
    WORKFLOW_PAIN = "WORKFLOW_PAIN"
    SERVICE_GAP = "SERVICE_GAP"
    TECH_SUPPORT = "TECH_SUPPORT"
    BUG_REPORT = "BUG_REPORT"
    CONTENT_REQUEST = "CONTENT_REQUEST"
    OTHER = "OTHER"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contains_pain: bool
    classification: Classification
    product_solvable: bool
    problem: str
    audience: str
    workaround: str | None
    quote: str
    frequency: str | None
    loss: str | None
    willingness_to_pay: bool
    confidence: float = Field(ge=0, le=1)


class ProblemMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_problem: bool


def _post(endpoint: str, payload: dict) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for analysis")

    client_kwargs = {
        "timeout": 60,
    }

    if settings.openai_proxy_url:
        client_kwargs["proxy"] = settings.openai_proxy_url

    with httpx.Client(**client_kwargs) as http:
        resp = http.post(
            "https://api.openai.com/v1/" + endpoint,
            headers={
                "Authorization": "Bearer " + settings.openai_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        # Важно: сохраняем тело ответа OpenAI в ошибке,
        # чтобы при 400/403/429 сразу видеть настоящую причину.
        if not resp.is_success:
            raise RuntimeError(
                f"OpenAI API error {resp.status_code}: {resp.text}"
            )

        return resp.json()


def budget_available(db: Session) -> bool:
    start = datetime.now(UTC).replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    spent = db.scalar(
        select(
            func.coalesce(
                func.sum(AiUsage.estimated_usd),
                0,
            )
        ).where(AiUsage.created_at >= start)
    )

    return float(spent or 0) < settings.monthly_ai_budget_usd


def record_usage(
    db: Session,
    model: str,
    usage: dict,
    input_per_m: float,
    output_per_m: float = 0,
) -> None:
    tokens_in = usage.get(
        "prompt_tokens",
        usage.get("input_tokens", 0),
    )

    tokens_out = usage.get(
        "completion_tokens",
        usage.get("output_tokens", 0),
    )

    db.add(
        AiUsage(
            model=model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            estimated_usd=(
                tokens_in * input_per_m
                + tokens_out * output_per_m
            ) / 1_000_000,
        )
    )


def analyze(db: Session, text: str) -> Finding:
    if not budget_available(db):
        raise RuntimeError("Monthly AI budget reached")

    instruction = (
        "Classify the original author's concrete problem and extract evidence for it. "
        "Comments may confirm frequency, workarounds, loss, willingness to pay, or related "
        "use cases, but a different problem mentioned only in a comment is not the original "
        "publication's problem. Use only evidence in the input. "
        "PRODUCT_OPPORTUNITY is a problem that could become a standalone software product. "
        "WORKFLOW_PAIN is a manual, fragmented, repetitive, or inconvenient workflow. "
        "SERVICE_GAP means the author cannot find a suitable service. FEATURE_REQUEST means "
        "a missing feature in an existing product. Set product_solvable=true for FEATURE_REQUEST "
        "only when the underlying problem is broad enough for a separate software product; "
        "narrow product-specific requests such as adding dark mode must be false. "
        "TECH_SUPPORT is configuration or usage help, BUG_REPORT is a specific software bug, "
        "CONTENT_REQUEST asks for information or content, and OTHER covers the rest. "
        "Set contains_pain=false and product_solvable=false when there is no concrete problem. "
        "Quote must be an exact substring of the input. "
        "Do not infer payment or measurable losses without explicit evidence. "
        "For self-hosted solution requests, extract the underlying need rather than merely "
        "restating the desired technology. Use the original language."
    )

    payload = {
        "model": settings.openai_model,
        "instructions": instruction,

        "input": (
            "Analyze the following publication.\n\n"
            + text[:6000]
        ),

        "text": {
            "format": {
                "type": "json_schema",
                "name": "finding",
                "strict": True,
                "schema": Finding.model_json_schema(),
            }
        },
    }

    data = _post("responses", payload)

    content = "".join(
        c.get("text", "")
        for output in data.get("output", [])
        for c in output.get("content", [])
        if c.get("type") == "output_text"
    )

    if not content:
        raise RuntimeError(
            f"OpenAI returned no output_text: {data}"
        )

    result = Finding.model_validate_json(content)

    # Актуально для gpt-5.6-luna:
    # $0.20 / 1M input tokens
    # $1.20 / 1M output tokens
    record_usage(
        db,
        settings.openai_model,
        data.get("usage", {}),
        0.20,
        1.20,
    )

    if result.contains_pain and (
        not result.quote
        or result.quote not in text
    ):
        return result.model_copy(update={
            "contains_pain": False,
            "classification": Classification.OTHER,
            "product_solvable": False,
        })

    return result


def same_problem(
    db: Session,
    evidence_problem: str,
    evidence_audience: str,
    cluster_title: str,
    cluster_audience: str,
    cluster_description: str,
) -> bool:
    if not budget_available(db):
        raise RuntimeError("Monthly AI budget reached")

    data = _post(
        "responses",
        {
            "model": settings.openai_model,
            "instructions": (
                "Decide whether the evidence and cluster describe the same concrete user problem. "
                "Matching technology, audience, or broad error category is not enough. "
                "Different root causes or failure modes are different problems. "
                "Different wording or language for the same problem is a match."
            ),
            "input": (
                f"EVIDENCE PROBLEM: {evidence_problem}\n"
                f"EVIDENCE AUDIENCE: {evidence_audience}\n"
                f"CLUSTER TITLE: {cluster_title}\n"
                f"CLUSTER AUDIENCE: {cluster_audience}\n"
                f"CLUSTER DESCRIPTION: {cluster_description}"
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "problem_match",
                    "strict": True,
                    "schema": ProblemMatch.model_json_schema(),
                }
            },
        },
    )
    record_usage(db, settings.openai_model, data.get("usage", {}), 0.20, 1.20)
    content = "".join(
        c.get("text", "")
        for output in data.get("output", [])
        for c in output.get("content", [])
        if c.get("type") == "output_text"
    )
    if not content:
        raise RuntimeError(f"OpenAI returned no output_text: {data}")
    return ProblemMatch.model_validate_json(content).same_problem


def embed(db: Session, text: str) -> list[float]:
    if not budget_available(db):
        raise RuntimeError("Monthly AI budget reached")

    data = _post(
        "embeddings",
        {
            "model": settings.openai_embedding_model,
            "input": text[:6000],
            "dimensions": settings.embedding_dimensions,
        },
    )

    record_usage(
        db,
        settings.openai_embedding_model,
        data.get("usage", {}),
        0.02,
    )

    return data["data"][0]["embedding"]
