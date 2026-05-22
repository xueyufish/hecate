"""Central application configuration powered by pydantic-settings.

Loads settings from environment variables and an optional ``.env`` file,
providing typed access to database, vector store, object storage, LLM, and
security configuration across the entire application.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables and ``.env``.

    Configuration groups:

    - **Database**: ``DATABASE_URL`` — async PostgreSQL connection string.
    - **Vector Store**: ``QDRANT_URL`` — Qdrant vector database endpoint used
      for embedding search.
    - **Object Storage**: ``MINIO_URL``, ``MINIO_ACCESS_KEY``,
      ``MINIO_SECRET_KEY``, ``MINIO_BUCKET`` — MinIO/S3-compatible storage
      for uploaded files and parsed documents.
    - **LLM**: ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY`` — API keys for LLM
      providers.
    - **Security**: ``HECATE_API_KEYS`` — comma-separated API keys for
      authenticating requests; ``LLM_GUARD_ENABLED`` — toggle input/output
      guardrails; ``RATE_LIMIT_RPM`` — per-key rate limit (requests per
      minute).
    """

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
        """Split the comma-separated ``HECATE_API_KEYS`` string into a list.

        Whitespace around each key is stripped and empty entries are
        discarded, so ``"key1, key2,"`` yields ``["key1", "key2"]``.
        """
        return [k.strip() for k in self.HECATE_API_KEYS.split(",") if k.strip()]


settings = Settings()
