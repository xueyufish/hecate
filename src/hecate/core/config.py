from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://hecate:hecate@localhost:5432/hecate"
    QDRANT_URL: str = "http://localhost:6333"
    MINIO_URL: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "hecate"

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    HECATE_API_KEYS: str = ""
    LLM_GUARD_ENABLED: bool = True
    RATE_LIMIT_RPM: int = 60

    @property
    def api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.HECATE_API_KEYS.split(",") if k.strip()]


settings = Settings()
