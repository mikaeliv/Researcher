from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researcher.config import settings
from researcher.models import AiUsage


class Finding(BaseModel):
    contains_pain: bool
    problem: str = ""
    audience: str = ""
    workaround: str | None = None
    quote: str = ""
    frequency: str | None = None
    loss: str | None = None
    willingness_to_pay: bool = False
    confidence: float = Field(default=0, ge=0, le=1)


def _post(endpoint: str, payload: dict) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for analysis")
    client_kwargs = {"timeout": 60}
    if settings.openai_proxy_url:
        client_kwargs["proxy"] = settings.openai_proxy_url
    with httpx.Client(**client_kwargs) as http:
        resp = http.post("https://api.openai.com/v1/" + endpoint,
                         headers={"Authorization": "Bearer " + settings.openai_api_key}, json=payload)
        resp.raise_for_status()
        return resp.json()


def budget_available(db: Session) -> bool:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = db.scalar(select(func.coalesce(func.sum(AiUsage.estimated_usd), 0)).where(AiUsage.created_at >= start))
    return float(spent or 0) < settings.monthly_ai_budget_usd


def record_usage(db: Session, model: str, usage: dict, input_per_m: float, output_per_m: float = 0) -> None:
    tokens_in = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    tokens_out = usage.get("completion_tokens", usage.get("output_tokens", 0))
    db.add(AiUsage(model=model, input_tokens=tokens_in, output_tokens=tokens_out,
                   estimated_usd=(tokens_in * input_per_m + tokens_out * output_per_m) / 1_000_000))


def analyze(db: Session, text: str) -> Finding:
    if not budget_available(db):
        raise RuntimeError("Monthly AI budget reached")
    instruction = ("Extract an actual problem that a digital product might solve. Use only evidence in the text. "
                   "Return contains_pain=false if there is no concrete problem. Quote must be an exact substring "
                   "of the input. Do not infer payment or measurable losses without explicit evidence. "
                   "Use the original language. Return JSON matching the schema.")
    payload = {"model": settings.openai_model, "instructions": instruction + " Schema: " + str(Finding.model_json_schema()),
               "input": text[:6000], "text": {"format": {"type": "json_object"}}}
    data = _post("responses", payload)
    content = "".join(c.get("text", "") for output in data.get("output", [])
                      for c in output.get("content", []) if c.get("type") == "output_text")
    result = Finding.model_validate_json(content)
    # These are estimates. Change rates in config/code when choosing another model.
    record_usage(db, settings.openai_model, data.get("usage", {}), 0.20, 1.20)
    if result.contains_pain and (not result.quote or result.quote not in text):
        return Finding(contains_pain=False)
    return result


def embed(db: Session, text: str) -> list[float]:
    if not budget_available(db):
        raise RuntimeError("Monthly AI budget reached")
    data = _post("embeddings", {"model": settings.openai_embedding_model,
                                "input": text[:6000], "dimensions": settings.embedding_dimensions})
    record_usage(db, settings.openai_embedding_model, data.get("usage", {}), 0.02)
    return data["data"][0]["embedding"]
