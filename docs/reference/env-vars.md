# Environment Variables

All configuration is loaded from environment variables and an optional `.env` file. Copy `.env.example` to `.env` and edit as needed.

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://hecate:hecate@localhost:5432/hecate` | Async DB connection string. Supports PostgreSQL, MySQL, SQLite. |
| `POSTGRES_PASSWORD` | `hecate` | PostgreSQL password (used by Docker Compose). |

## Vector Store

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_STORE_TYPE` | `qdrant` | Backend: `qdrant` or `chroma`. |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint. |
| `QDRANT_API_KEY` | — | Qdrant API key (if using Qdrant Cloud). |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | Chroma persistence directory. |

## Object Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_URL` | `localhost:9000` | MinIO / S3-compatible endpoint. |
| `MINIO_ACCESS_KEY` | — | MinIO access key. |
| `MINIO_SECRET_KEY` | — | MinIO secret key. |
| `MINIO_BUCKET` | `hecate` | Default bucket name. |

## LLM Providers

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key. |
| `ANTHROPIC_API_KEY` | Anthropic API key. |
| `DEEPSEEK_API_KEY` | DeepSeek API key. |
| `DASHSCOPE_API_KEY` | Alibaba DashScope (Qwen) API key. |
| `ZAI_API_KEY` | Zhipu (GLM) API key. |

For providers not listed here, see the [LiteLLM provider documentation](https://docs.litellm.ai/docs/providers).

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `HECATE_API_KEYS` | — | Comma-separated API keys for authenticating requests. |
| `JWT_SECRET` | — | JWT signing secret. |
| `LLM_GUARD_ENABLED` | `true` | Enable input/output prompt scanning. |
| `RATE_LIMIT_RPM` | `60` | Per-key requests per minute. |

## Data Loss Prevention

The DLP engine scans content at every trust boundary for sensitive data and applies a per-entity policy. See [DLP](../concepts/dlp.md). Fails open by default.

| Variable | Default | Description |
|----------|---------|-------------|
| `DLP_ENABLED` | `true` | Master switch. `false` disables all DLP scanning and falls back to legacy `PIIAnonymizer`. |
| `DLP_DISABLED_ENTITIES` | `""` | Comma-separated entity types to skip globally. |
| `DLP_INPUT_HOOK_ENABLED` | `true` | Scan inbound messages (PreLLM boundary). |
| `DLP_OUTPUT_HOOK_ENABLED` | `true` | Scan LLM responses after deanonymization (PostLLM boundary). |
| `DLP_TOOL_RESULT_HOOK_ENABLED` | `true` | Scan tool outputs (PostTool boundary). |
| `DLP_MCP_RESPONSE_FILTER_ENABLED` | `true` | Scan MCP tool-server responses via `DLPEgressFilter`. |
| `DLP_STREAM_ENABLED` | `true` | Incremental streaming scan for token-streamed output. |
| `DLP_STREAM_BUFFER_SIZE` | `300` | Characters buffered per incremental scan. |
| `DLP_STREAM_OVERLAP` | `10` | Overlap between buffers to catch cross-boundary patterns. |
| `DLP_STREAM_FINAL_SCAN` | `true` | Run a full scan at stream end as a backstop. |
| `DLP_STREAM_MASK_CORRECTION` | `false` | Emit a corrective message after a streamed MASK detection. |

## Session State Store

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_STATE_STORE_BACKEND` | `memory` | Backend: `memory`, `redis`, `postgres`, `tiered`. |
| `SESSION_STATE_REDIS_URL` | — | Redis connection URL (when backend is `redis` or `tiered`). |
| `SESSION_STATE_TTL_DAYS` | `7` | Idle session TTL. |

## Event Store

| Variable | Default | Description |
|----------|---------|-------------|
| `EVENT_STORE_BACKEND` | `memory` | Backend: `memory` or `postgres`. |
| `EVENT_STORE_PG_TABLE` | `events` | PostgreSQL table name for events. |

## MCP Server

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_SERVER_ENABLED` | `false` | Enable Hecate as an MCP server. |
| `MCP_SERVER_HOST` | `0.0.0.0` | MCP server bind address. |
| `MCP_SERVER_PORT` | `8000` | MCP server port. |
| `MCP_TRANSPORT` | `http` | Transport: `http` (Streamable HTTP). |

## A2A Server

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_SERVER_ENABLED` | `false` | Enable A2A protocol server. |

## Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACING_ENABLED` | `false` | Enable OpenTelemetry tracing. |
| `TRACE_DB_EXPORT_ENABLED` | `false` | Export traces to PostgreSQL. |

## Sandbox

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_POOL_ENABLED` | `false` | Enable Docker sandbox container pool. |