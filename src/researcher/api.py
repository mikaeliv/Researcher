from fastapi import FastAPI

app = FastAPI(title="Researcher")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

