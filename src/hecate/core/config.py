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
    - **Vector Store**: ``VECTOR_STORE_TYPE`` — backend selector (``qdrant``
      or ``chroma``); ``QDRANT_URL``, ``QDRANT_API_KEY`` for Qdrant;
      ``CHROMA_PERSIST_DIR`` for Chroma.
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

    VECTOR_STORE_TYPE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    CHROMA_PERSIST_DIR: str = "./data/chroma"

    MINIO_URL: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "hecate"

    HECATE_API_KEYS: str = ""
    JWT_SECRET: str = ""
    LLM_GUARD_ENABLED: bool = True
    RATE_LIMIT_RPM: int = 60

    MCP_SERVER_ENABLED: bool = False
    MCP_SERVER_HOST: str = "0.0.0.0"  # noqa: S104
    MCP_SERVER_PORT: int = 8000
    MCP_AUTH_TYPE: str = "api_key"
    MCP_TRANSPORT: str = "http"
    MCP_CLIENT_TIMEOUT: int = 30

    TEMPORAL_SERVER_URL: str = "localhost:7233"
    TEMPORAL_TASK_QUEUE: str = "hecate-workers"
    TEMPORAL_HEARTBEAT_TIMEOUT: int = 30
    TEMPORAL_START_TO_CLOSE_TIMEOUT: int = 300

    FERNET_KEY: str = ""

    WORKSPACE_ROOT: str = "./workspace"
    SEARCH_PROVIDER: str = "duckduckgo"
    SEARCH_API_KEY: str = ""

    TRACING_ENABLED: bool = True

    @property
    def api_keys_list(self) -> list[str]:
        """Split the comma-separated ``HECATE_API_KEYS`` string into a list."""
        return [k.strip() for k in self.HECATE_API_KEYS.split(",") if k.strip()]


settings = Settings()
