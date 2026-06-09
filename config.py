"""
Central configuration — reads from .env file.
All settings have sensible defaults so the system runs without any .env for demo.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    llm_model: str = Field(default="llama-3.3-70b-versatile", alias="LLM_MODEL")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./invoice_agent.db", alias="DATABASE_URL"
    )

    # Redis
    use_fake_redis: bool = Field(default=True, alias="USE_FAKE_REDIS")
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")

    # API
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_reload: bool = Field(default=True, alias="API_RELOAD")

    # Agent behavior
    agent_max_retries: int = Field(default=3, alias="AGENT_MAX_RETRIES")
    anomaly_sensitivity: str = Field(default="medium", alias="ANOMALY_SENSITIVITY")
    auto_approve_threshold: float = Field(default=1000.0, alias="AUTO_APPROVE_THRESHOLD")

    # Observability
    enable_tracing: bool = Field(default=True, alias="ENABLE_TRACING")
    otlp_endpoint: str = Field(default="http://localhost:4317", alias="OTLP_ENDPOINT")

    # Demo
    demo_mode: bool = Field(default=True, alias="DEMO_MODE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True


settings = Settings()
