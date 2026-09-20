FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN pip install --no-cache-dir .
RUN useradd --create-home researcher
USER researcher
CMD ["uvicorn", "researcher.api:app", "--host", "0.0.0.0", "--port", "8000"]
