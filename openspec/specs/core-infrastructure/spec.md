## Purpose

Core infrastructure provides the foundational application layer — configuration management (pydantic-settings), async database engine creation, authentication (API Key + JWT), user context resolution, agent lookup with soft-delete filtering, and in-memory rate limiting.
## Requirements
### Requirement: Settings loaded from environment variables and .env file
The `Settings` class (pydantic-settings) SHALL include `VECTOR_STORE_TYPE` (default `"qdrant"`) and per-backend connection settings: `QDRANT_URL` (default `"http://localhost:6333"`), `QDRANT_API_KEY` (default `""`), and `CHROMA_PERSIST_DIR` (default `"./data/chroma"`). The existing `QDRANT_URL` field SHALL be retained for backward compatibility when `VECTOR_STORE_TYPE=qdrant`.

#### Scenario: Default values
- **WHEN** no environment variables are set
- **THEN** `Settings` SHALL use defaults: `VECTOR_STORE_TYPE="qdrant"`, `QDRANT_URL="http://localhost:6333"`, `CHROMA_PERSIST_DIR="./data/chroma"`

#### Scenario: API keys parsed from comma-separated string
- **WHEN** `HECATE_API_KEYS` is set to "key1,key2,key3"
- **THEN** `settings.api_keys_list` SHALL return `["key1", "key2", "key3"]`

#### Scenario: Empty API keys
- **WHEN** `HECATE_API_KEYS` is empty or unset
- **THEN** `settings.api_keys_list` SHALL return `[]`

#### Scenario: Qdrant configuration
- **WHEN** `VECTOR_STORE_TYPE=qdrant` and `QDRANT_URL=http://custom:6333`
- **THEN** the QdrantVectorStore SHALL connect to the specified URL

#### Scenario: Chroma configuration
- **WHEN** `VECTOR_STORE_TYPE=chroma` and `CHROMA_PERSIST_DIR=/data/vecs`
- **THEN** the ChromaVectorStore SHALL use `/data/vecs` as the persistence directory

#### Scenario: Unsupported vector store type
- **WHEN** `VECTOR_STORE_TYPE` is set to an unrecognized value
- **THEN** `get_vector_store()` SHALL raise `ValueError` at runtime

### Requirement: Async database engine with auto-commit session
The `engine` module SHALL create an async SQLAlchemy engine from `DATABASE_URL` using a factory function that applies dialect-appropriate pool settings. PostgreSQL and MySQL SHALL use pool_size=20, max_overflow=10. SQLite SHALL use no connection pooling (StaticPool for in-memory, default for file-based). The `get_db()` FastAPI dependency SHALL remain unchanged — it SHALL auto-commit on success and auto-rollback on error.

#### Scenario: Successful request commits session
- **WHEN** a FastAPI handler completes without exception using `get_db()` dependency
- **THEN** the session SHALL be committed automatically

#### Scenario: Failed request rolls back session
- **WHEN** a FastAPI handler raises an exception
- **THEN** the session SHALL be rolled back and the exception re-raised

#### Scenario: PostgreSQL engine creation
- **WHEN** `DATABASE_URL` starts with `postgresql+asyncpg://`
- **THEN** the engine SHALL be created with `pool_size=20, max_overflow=10`

#### Scenario: MySQL engine creation
- **WHEN** `DATABASE_URL` starts with `mysql+aiomysql://`
- **THEN** the engine SHALL be created with `pool_size=20, max_overflow=10`

#### Scenario: SQLite in-memory engine creation
- **WHEN** `DATABASE_URL` is `sqlite+aiosqlite://`
- **THEN** the engine SHALL be created with `connect_args={"check_same_thread": False}` and `poolclass=StaticPool`

#### Scenario: SQLite file-based engine creation
- **WHEN** `DATABASE_URL` starts with `sqlite+aiosqlite:///` (with a file path)
- **THEN** the engine SHALL be created without connection pool overrides

#### Scenario: Unsupported dialect
- **WHEN** `DATABASE_URL` uses an unsupported scheme
- **THEN** a `ValueError` SHALL be raised at import time listing supported dialects

### Requirement: Dual authentication via API Key or JWT
The `verify_api_key` dependency SHALL accept both API Key and JWT Bearer token authentication.

#### Scenario: Valid API Key
- **WHEN** `Authorization: Bearer <hecate-api-key>` is provided and the key exists in `settings.api_keys_list`
- **THEN** the request SHALL be authenticated and the token string returned

#### Scenario: Valid JWT token
- **WHEN** `Authorization: Bearer <jwt-token>` is provided and `decode_access_token(token)` succeeds
- **THEN** the request SHALL be authenticated and the token string returned

#### Scenario: Invalid credentials
- **WHEN** neither API key nor JWT is valid
- **THEN** the dependency SHALL raise HTTPException with status 401 and error code "UNAUTHORIZED"

### Requirement: User ID extraction from JWT or API Key
The `get_current_user_id` dependency SHALL extract user ID from JWT, falling back to a placeholder UUID for API Key auth.

#### Scenario: JWT with user ID
- **WHEN** a valid JWT token with `"sub"` claim is provided
- **THEN** the dependency SHALL return `uuid.UUID(payload["sub"])`

#### Scenario: API Key fallback
- **WHEN** a valid API key is provided (not JWT)
- **THEN** the dependency SHALL return `uuid.UUID("00000000-0000-0000-0000-000000000000")`

### Requirement: Agent retrieval by path parameter
The `get_current_agent` dependency SHALL retrieve an agent by UUID, excluding soft-deleted agents.

#### Scenario: Agent found
- **WHEN** a valid agent_id is provided and the agent is not soft-deleted
- **THEN** the dependency SHALL return the `AgentModel` instance

#### Scenario: Agent not found
- **WHEN** the agent_id does not exist or the agent is soft-deleted
- **THEN** the dependency SHALL raise HTTPException with status 404 and error code "NOT_FOUND"

### Requirement: In-memory rate limiting per API key
The `RateLimiter` SHALL enforce per-key rate limiting using a sliding window of 60 seconds.

#### Scenario: Request within limit
- **WHEN** a key has made fewer than `requests_per_minute` requests in the last 60 seconds
- **THEN** `is_allowed(key)` SHALL return True and record the timestamp

#### Scenario: Request exceeds limit
- **WHEN** a key has made `requests_per_minute` or more requests in the last 60 seconds
- **THEN** `is_allowed(key)` SHALL return False

#### Scenario: Rate limit exceeded returns 429
- **WHEN** `check_rate_limit` dependency detects rate limit exceeded
- **THEN** it SHALL raise HTTPException with status 429, error code "RATE_LIMIT_EXCEEDED", and `Retry-After` header

