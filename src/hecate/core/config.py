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

    METRICS_STORE_TYPE: str = "in_memory"  # "in_memory" | "timescale"
    METRICS_PUSH_INTERVAL: int = 5  # seconds between WebSocket metric pushes
    MAX_METRICS_BUFFER_SIZE: int = 100000  # max entries per InMemory ring buffer

    # Alerting configuration
    ALERT_ENABLED: bool = True
    ALERT_EVAL_INTERVAL: int = 60  # seconds between alert evaluation cycles
    ALERT_SMTP_HOST: str = ""
    ALERT_SMTP_PORT: int = 587
    ALERT_SMTP_USER: str = ""
    ALERT_SMTP_PASSWORD: str = ""
    ALERT_SMTP_FROM: str = "alerts@hecate.local"

    # Quota management configuration
    QUOTA_ENABLED: bool = True
    QUOTA_DEFAULT_WORKSPACE_RPM: int = 60
    QUOTA_CACHE_TTL: int = 60

    # Audit configuration
    AUDIT_ENABLED: bool = True
    AUDIT_BATCH_SIZE: int = 50
    AUDIT_FLUSH_INTERVAL_SECONDS: float = 2.0
    AUDIT_QUEUE_MAX_SIZE: int = 10000
    AUDIT_RETENTION_DAYS: int = 90
    AUDIT_ARCHIVE_ENABLED: bool = False
    AUDIT_ARCHIVE_STORAGE: str = "minio"
    AUDIT_ARCHIVE_PATH: str = "audit-archives"

    # Prompt management
    PROTECTED_PROMPT_LABELS: list[str] = ["production"]

    # SSO / OIDC configuration
    SSO_OIDC_CLIENT_ID: str = ""
    SSO_OIDC_CLIENT_SECRET: str = ""
    SSO_OIDC_DISCOVERY_URL: str = ""
    SSO_OIDC_SCOPE: str = "openid profile email"

    # SSO / SAML configuration
    SSO_SAML_SP_ENTITY_ID: str = ""
    SSO_SAML_SP_ACS_URL: str = ""
    SSO_SAML_IDP_ENTITY_ID: str = ""
    SSO_SAML_IDP_SSO_URL: str = ""
    SSO_SAML_IDP_X509_CERT: str = ""

    # SSO / LDAP configuration
    SSO_LDAP_SERVER_URL: str = ""
    SSO_LDAP_BASE_DN: str = ""
    SSO_LDAP_BIND_DN: str = ""
    SSO_LDAP_BIND_PASSWORD: str = ""
    SSO_LDAP_SEARCH_FILTER: str = "(uid={})"
    SSO_LDAP_USE_SSL: bool = True

    # SCIM configuration
    SCIM_ENABLED: bool = False
    SCIM_BEARER_TOKEN: str = ""

    # Router cache configuration
    ROUTER_CACHE_ENABLED: bool = True
    ROUTER_CACHE_TTL: int = 300
    ROUTER_CACHE_REDIS_URL: str = ""
    ROUTER_CACHE_FALLBACK_TO_MEMORY: bool = True
    ROUTER_COST_AWARE: bool = True
    VAULT_URL: str = ""
    VAULT_TOKEN: str = ""
    VAULT_ROLE_ID: str = ""
    VAULT_SECRET_ID: str = ""
    VAULT_MOUNT_POINT: str = "secret"
    VAULT_CACHE_TTL: int = 300
    VAULT_FALLBACK_TO_SETTINGS: bool = True

    # AWS Secrets Manager
    AWS_SECRETS_REGION: str = ""
    AWS_SECRETS_ACCESS_KEY_ID: str = ""
    AWS_SECRETS_SECRET_ACCESS_KEY: str = ""

    # Azure Key Vault
    AZURE_KEYVAULT_URL: str = ""

    # A2A Protocol configuration
    A2A_SERVER_ENABLED: bool = False
    A2A_SERVER_URL: str = "http://localhost:8000"
    A2A_AGENT_NAME: str = "Hecate Agent"
    A2A_AUTH_MODE: str = "api_key"
    A2A_SIGNING_ENABLED: bool = False
    A2A_SIGNING_KEY_PATH: str = ""
    A2A_JWKS_CACHE_TTL: int = 3600

    COST_ANOMALY_THRESHOLD: float = 2.5
    COST_ROLLING_WINDOW_DAYS: int = 30
    COST_DEFAULT_POLICY: str = "alert"

    @property
    def api_keys_list(self) -> list[str]:
        """Split the comma-separated ``HECATE_API_KEYS`` string into a list."""
        return [k.strip() for k in self.HECATE_API_KEYS.split(",") if k.strip()]


settings = Settings()
