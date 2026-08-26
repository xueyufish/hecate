"""Central application configuration powered by pydantic-settings.

Loads settings from environment variables and an optional ``.env`` file,
providing typed access to database, vector store, object storage, LLM, and
security configuration across the entire application.

This module also bridges ``.env`` values into ``os.environ`` so that
third-party SDKs (LiteLLM and the providers it wraps) can read credentials
the same way they do under Docker Compose's ``env_file:`` injection.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


def bridge_dotenv_to_environ(
    env_file: str = _ENV_FILE,
    environ: MutableMapping[str, str] | None = None,
) -> int:
    """Export ``.env`` values into the process environment.

    pydantic-settings loads ``.env`` only into the ``Settings`` object, while
    third-party SDKs (LiteLLM) read provider credentials from ``os.environ``
    directly — without this bridge, keys placed in ``.env`` never reach them
    when running on the host (Docker Compose performs the equivalent injection
    via ``env_file:``).

    Real environment variables always win; empty values are skipped so an
    unset credential stays unset rather than becoming a blank string.

    Returns:
        Number of variables exported.
    """
    env = os.environ if environ is None else environ
    exported = 0
    for key, value in dotenv_values(env_file).items():
        if value and key not in env:
            env[key] = value
            exported += 1
    return exported


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
    - **LLM**: provider API keys (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
      ``ZAI_API_KEY``, ...) are not Settings fields — they are consumed by
      LiteLLM via ``os.environ`` and reach it through the module-level
      ``.env`` bridge (see :func:`bridge_dotenv_to_environ`).
    - **Security**: ``HECATE_API_KEYS`` — comma-separated API keys for
      authenticating requests; ``LLM_GUARD_ENABLED`` — toggle input/output
      guardrails; ``RATE_LIMIT_RPM`` — per-key rate limit (requests per
      minute).
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://hecate:hecate@localhost:5432/hecate"

    # SessionStateStore backend selection (session-state-store & horizontal-scaling changes)
    SESSION_STATE_STORE_BACKEND: str = "memory"  # "memory" | "redis" | "postgres" | "tiered"
    SESSION_STATE_TTL_DAYS: int = 7  # idle TTL applied to both Redis EX and PG query filter
    SESSION_STATE_REDIS_URL: str = ""  # Redis connection URL (e.g., "redis://localhost:6379/0")
    SESSION_STATE_KEY_PREFIX: str = "hecate:state:"  # Redis key prefix for multi-app isolation

    # EventStore backend selection (eventstore-pg-wiring change)
    EVENT_STORE_BACKEND: str = "memory"  # "memory" | "postgres"
    EVENT_STORE_PG_TABLE: str = "events"  # PG table name (operator-customizable)

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
    SHUTDOWN_DRAIN_TIMEOUT: int = 30

    MCP_SERVER_ENABLED: bool = False
    MCP_SERVER_HOST: str = "0.0.0.0"  # noqa: S104
    MCP_SERVER_PORT: int = 8000
    MCP_AUTH_TYPE: str = "api_key"
    MCP_TRANSPORT: str = "http"
    MCP_CLIENT_TIMEOUT: int = 30
    MCP_POOL_MIN_SIZE: int = 1
    MCP_POOL_MAX_SIZE: int = 5
    MCP_BORROW_TIMEOUT: int = 5
    MCP_HEALTH_CHECK_INTERVAL: int = 30
    MCP_RECONNECT_MAX_RETRIES: int = 5
    MCP_RECONNECT_BASE_DELAY: float = 1.0
    MCP_RECONNECT_MAX_DELAY: float = 60.0
    MCP_REQUEST_TIMEOUT: int = 30
    MCP_TOOL_CACHE_TTL: int = 300
    MCP_CIRCUIT_BREAKER_THRESHOLD: int = 5
    MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = 30

    TOOL_CACHE_ENABLED: bool = True
    TOOL_CACHE_DEFAULT_TTL: int = 300
    TOOL_CACHE_MAX_ENTRIES: int = 10000
    TOOL_CACHE_SESSION_SCOPED: bool = True

    HOOK_SHELL_ENABLED: bool = False
    HOOK_SHELL_TIMEOUT: int = 30

    AGENT_ENV_ENABLED: bool = True
    AGENT_ENV_TTL: int = 86400
    AGENT_ENV_BACKEND: str = "local"  # "local" or "docker"

    DOCKER_AGENT_IMAGE: str = "python:3.12-slim"
    DOCKER_RUNTIME: str = "runc"  # "runc" (namespace) or "runsc" (gVisor user-space kernel)
    DOCKER_NETWORK_MODE: str = "none"  # Docker network mode: "none", "bridge", "host"
    DOCKER_WARM_POOL_SIZE: int = 10
    DOCKER_WARM_POOL_IDLE_TIMEOUT: int = 3600

    # Environment Security (5.9 P0): network egress, audit pipeline, credential
    # scoping, and sandbox enforcement. All default to backward-compatible values.
    AGENT_ENV_NETWORK_POLICY: str = "allow_all"  # "allow_all" or "deny_all"
    AGENT_ENV_DECISION_ENABLED: bool = True
    AGENT_ENV_DECISION_RETENTION_DAYS: int = 30
    AGENT_ENV_DECISION_BATCH_SIZE: int = 50
    AGENT_ENV_DECISION_FLUSH_INTERVAL: float = 5.0
    AGENT_ENV_CREDENTIAL_SCOPING: bool = False
    AGENT_ENV_SANDBOX_ENFORCEMENT: bool = False

    # Meta-agents (13.9a-d): lifecycle ops agents run by MetaAgentScheduler
    # in the app lifespan. Default off — opt-in per deployment. The drift
    # detector is intentionally not registered yet (needs an expected-
    # baseline source); GC and compliance checker are wired.
    META_AGENTS_ENABLED: bool = False
    META_AGENTS_INTERVAL_SECONDS: int = 3600

    # SIEM Export Pipeline (8.7): security event export to external SIEM.
    SIEM_ENABLED: bool = False
    SIEM_EXPORTERS: str = ""  # comma-separated: "webhook,syslog,ocsf"
    SIEM_FILTER_EVENT_TYPES: str = ""  # comma-separated: "api,tool_policy,anomaly"
    SIEM_MIN_SEVERITY: str = "info"  # info | low | medium | high | critical
    SIEM_BATCH_SIZE: int = 50
    SIEM_FLUSH_INTERVAL: float = 5.0
    # Webhook exporter
    SIEM_WEBHOOK_URL: str = ""
    SIEM_WEBHOOK_TOKEN: str = ""
    SIEM_WEBHOOK_FORMAT: str = "json"  # "json" or "splunk_hec"
    SIEM_WEBHOOK_HEADERS: str = ""  # JSON string of extra headers
    # Syslog exporter
    SIEM_SYSLOG_HOST: str = "localhost"
    SIEM_SYSLOG_PORT: int = 514
    SIEM_SYSLOG_PROTOCOL: str = "tcp"  # "tcp" or "udp"
    SIEM_SYSLOG_TLS: bool = False
    SIEM_SYSLOG_FACILITY: int = 4  # security/authorization
    # Security Finding retention
    SECURITY_FINDING_RETENTION_DAYS: int = 90

    # Context Offloading (1.3.15b): overflow messages are written to the
    # AgentEnvironment filesystem instead of being discarded by compression.
    CONTEXT_OFFLOAD_ENABLED: bool = True
    CONTEXT_OFFLOAD_THRESHOLD_TOKENS: int = 6000

    # Sandbox Environment Mount (1.3.15c): mount agent environment into sandbox
    # containers at /mnt/env. Mode "rw" (default) allows sandbox to write output
    # files; "ro" restricts to read-only.
    SANDBOX_MOUNT_MODE: str = "rw"

    # Sandbox Container Pool (9.4d): pre-warmed Docker container pool for sandboxed
    # tool execution. Disabled by default; opt-in via SANDBOX_POOL_ENABLED=true.
    SANDBOX_POOL_ENABLED: bool = False
    SANDBOX_POOL_SIZE: int = 3
    SANDBOX_MAX_USES: int = 50
    SANDBOX_POOL_IDLE_TIMEOUT: int = 300  # seconds before trimming excess idle containers
    SANDBOX_POOL_ACQUIRE_TIMEOUT: int = 30  # seconds to wait when pool exhausted (wait strategy)
    SANDBOX_POOL_BUSY_TTL: int = 1800  # seconds before force-releasing stale in_use containers
    SANDBOX_POOL_EXHAUSTION_STRATEGY: str = "wait"  # "wait" or "temporary"

    TEMPORAL_SERVER_URL: str = "localhost:7233"
    TEMPORAL_TASK_QUEUE: str = "hecate-workers"
    TEMPORAL_HEARTBEAT_TIMEOUT: int = 30
    TEMPORAL_START_TO_CLOSE_TIMEOUT: int = 300

    FERNET_KEY: str = ""

    # Outbound DLP Engine (4.x) — gate egress content for PII / secrets.
    DLP_ENABLED: bool = True
    DLP_STREAM_ENABLED: bool = True
    DLP_STREAM_BUFFER_SIZE: int = 300
    DLP_STREAM_OVERLAP: int = 10
    DLP_STREAM_FINAL_SCAN: bool = True
    DLP_STREAM_MASK_CORRECTION: bool = False
    # Entity suppressions: comma-separated entity_type names skipped by
    # the DLP scanner (e.g. "EMAIL,PHONE" for orgs that intentionally
    # allow outbound PII).
    DLP_DISABLED_ENTITIES: str = ""
    # When true, the input security hook delegates secrets detection to
    # the DLP scanner (boundary 1). Falls back to llm_guard_scanner
    # when the DLP scanner is not configured.
    DLP_INPUT_HOOK_ENABLED: bool = True
    DLP_OUTPUT_HOOK_ENABLED: bool = True
    DLP_TOOL_RESULT_HOOK_ENABLED: bool = True
    DLP_MCP_RESPONSE_FILTER_ENABLED: bool = True

    PLUGINS_DIR: str = "./plugins"
    HOT_RELOAD: bool = False
    # Module-name prefixes permitted for runtime-loaded `python:` plugin entries
    # under self-hosted deployment. Effective only when SAAS_MODE=false;
    # ignored in SaaS mode (non-first-party entries are rejected outright).
    # Segment-boundary matched: `mycompany.` matches `mycompany.tools.x`
    # but not `mycompanyevil.x`. ADR-029 — runtime-installed artifacts
    # are never T0; this allowlist is the documented self-hosted exception.
    PLUGIN_PYTHON_ENTRY_ALLOWLIST: list[str] = []

    # Agent Plugins 1.0 ingestion (feature 5.5c) — go-live gate satisfied by
    # content scanning (5.13a): defaults on, doubles as the emergency
    # kill-switch.
    AGENT_PLUGINS_INGESTION_ENABLED: bool = True
    # Users allowed to install packages containing stdio MCP entries
    # (platform-level installs). Empty list means nobody may install stdio.
    PLATFORM_PLUGIN_INSTALLERS: list[str] = []
    # Allowed launcher commands for stdio MCP subprocesses (sandboxed).
    AGENT_PLUGIN_STDIO_COMMAND_ALLOWLIST: list[str] = ["npx", "uvx"]
    # Size caps for materialized package snapshots (MB).
    AGENT_PLUGIN_MAX_PACKAGE_MB: int = 100
    AGENT_PLUGIN_MAX_WORKSPACE_MB: int = 500
    # Plugin content scanning (feature 5.13a): severity threshold for the
    # block verdict ("high" | "medium"); findings below it warn.
    AGENT_PLUGIN_SCAN_BLOCK_AT: str = "high"
    # Per-file text scan cap (MB) — oversized text produces a finding
    # instead of being silently skipped.
    AGENT_PLUGIN_SCAN_FILE_CAP_MB: int = 1
    # Container image for sandboxed stdio MCP execution (needs Node+Python).
    AGENT_PLUGIN_RUNNER_IMAGE: str = "hecate-plugin-runner:latest"
    # SaaS deployment mode: stdio MCP entries are skipped with a warning.
    SAAS_MODE: bool = False

    WORKSPACE_ROOT: str = "./workspace"
    SEARCH_PROVIDER: str = "duckduckgo"
    SEARCH_API_KEY: str = ""

    TRACING_ENABLED: bool = True
    TRACE_DB_EXPORT_ENABLED: bool = True
    TRACE_DB_QUEUE_MAX_SIZE: int = 10000
    TRACE_DB_FLUSH_INTERVAL: int = 5  # seconds between TraceModel flush cycles

    # Agent health monitoring thresholds
    AGENT_HEALTH_ERROR_RATE_WARNING: float = 0.05
    AGENT_HEALTH_ERROR_RATE_CRITICAL: float = 0.15
    AGENT_HEALTH_LATENCY_WARNING_MS: int = 10000
    AGENT_HEALTH_LATENCY_CRITICAL_MS: int = 30000
    AGENT_HEALTH_SCORE_WEIGHTS: dict = {"error_rate": 0.5, "latency": 0.3, "activity": 0.2}

    # Conversation quality scoring
    CONVERSATION_QUALITY_SCORING_ENABLED: bool = True
    CONVERSATION_QUALITY_SAMPLING_RATE: float = 1.0  # 1.0 = 100% of conversations scored
    CONVERSATION_QUALITY_JUDGE_MODEL: str = "gpt-4o-mini"

    # Conversation topic clustering
    CONVERSATION_CLUSTERING_ENABLED: bool = True
    CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE: int = 10
    CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD: float = 0.5  # below this → unclassified
    CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD: float = 0.8  # above this → direct assign

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

    # Data Backup & Recovery (13.5): full system backup with per-tenant restore.
    BACKUP_STORAGE_TYPE: str = "minio"  # "minio" or "s3"
    BACKUP_MINIO_BUCKET: str = "hecate-backups"
    BACKUP_S3_ENDPOINT: str = ""
    BACKUP_S3_BUCKET: str = ""
    BACKUP_S3_ACCESS_KEY: str = ""
    BACKUP_S3_SECRET_KEY: str = ""
    BACKUP_S3_REGION: str = "us-east-1"
    BACKUP_SCHEDULE_ENABLED: bool = False
    BACKUP_SCHEDULE_CRON: str = "0 2 * * *"  # daily at 02:00
    BACKUP_RETENTION_HOURLY: int = 24
    BACKUP_RETENTION_DAILY: int = 14
    BACKUP_RETENTION_MONTHLY: int = 12
    BACKUP_VERIFY_ENABLED: bool = False
    BACKUP_VERIFY_SCHEDULE: str = "0 4 * * 0"  # weekly Sunday 04:00
    BACKUP_PG_DUMP_JOBS: int = 1  # parallel jobs for pg_restore

    @property
    def api_keys_list(self) -> list[str]:
        """Split the comma-separated ``HECATE_API_KEYS`` string into a list."""
        return [k.strip() for k in self.HECATE_API_KEYS.split(",") if k.strip()]


# Bridge .env into os.environ before Settings instantiation so the Settings
# object and third-party SDKs (LiteLLM) observe the same credential values.
bridge_dotenv_to_environ()

settings = Settings()
