"""Минимальный HTTP endpoint для проверки процесса API."""

from fastapi import FastAPI

app = FastAPI(title="Researcher")


@app.get("/health")
def health() -> dict:
    """Подтвердить, что API отвечает; БД и очередь здесь не проверяются."""
    return {"status": "ok"}
