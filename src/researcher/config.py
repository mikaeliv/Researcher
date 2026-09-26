from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://researcher:change-me@localhost:5432/researcher"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_proxy_url: str = ""
    embedding_dimensions: int = 1536
    telegram_bot_token: str = ""
    telegram_proxy_url: str = ""
    telegram_channel_id: str = ""
    telegram_owner_id: int | None = None
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "researcher/0.1 (contact: owner@example.com)"
    youtube_api_key: str = ""
    monthly_ai_budget_usd: float = 20
    cluster_candidate_top_k: int = 3
    cluster_candidate_threshold: float = 0.35
    min_cluster_evidence: int = 2
    min_cluster_sources: int = 2
    max_daily_posts: int = 5
    publish_score: int = 50


settings = Settings()
