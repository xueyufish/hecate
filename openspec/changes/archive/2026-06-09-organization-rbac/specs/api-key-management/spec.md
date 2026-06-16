## ADDED Requirements

### Requirement: API key database model
The system SHALL store API keys in an `ApiKeyModel` database table with the following fields: `id` (UUID PK), `name` (string, user-friendly label), `key_hash` (SHA-256 hash of the raw key), `key_prefix` (first 8 characters for display), `scope` (enum: `system` or `workspace`), `org_id` (nullable FK to OrganizationModel), `workspace_id` (nullable FK to WorkspaceModel), `created_by` (FK to UserModel), `last_used_at` (nullable datetime), `expires_at` (nullable datetime), `is_active` (boolean), plus inherited BaseModel fields (timestamps, soft delete).

#### Scenario: API key model fields
- **WHEN** an API key is created with `scope: "workspace"`
- **THEN** `org_id` and `workspace_id` are required and set to the specified organization and workspace

#### Scenario: System-scope key has no workspace binding
- **WHEN** an API key is created with `scope: "system"`
- **THEN** `org_id` and `workspace_id` are null

### Requirement: API key generation
The system SHALL generate API keys in the format `hcat_<base62_32chars>`. The raw key SHALL be shown to the user exactly once at creation time and never stored in plaintext. Only the SHA-256 hash SHALL be persisted.

#### Scenario: Create workspace API key
- **WHEN** a workspace admin sends POST `/api/api-keys` with `{name: "Production Key", scope: "workspace", workspace_id: "..."}`
- **THEN** the system generates a key, stores its SHA-256 hash and prefix, returns `201` with `{id, name, key: "hcat_...", scope, workspace_id, created_at}`. The raw key is not stored.

#### Scenario: Create system API key
- **WHEN** a user sends POST `/api/api-keys` with `{name: "Admin Key", scope: "system"}`
- **THEN** the system generates a system-scope key, returns `201` with the raw key

#### Scenario: Key shown only once
- **WHEN** a user creates an API key and subsequently calls GET `/api/api-keys/{id}`
- **THEN** the response includes `{id, name, key_prefix: "hcat_abcd...", scope, ...}` but NOT the full raw key

### Requirement: API key verification
The system SHALL verify API keys by computing SHA-256 of the incoming bearer token and comparing against stored `key_hash`. On successful verification, the system SHALL update `last_used_at` and return the full key context (scope, org_id, workspace_id).

#### Scenario: Valid workspace key
- **WHEN** a request arrives with `Authorization: Bearer hcat_<valid_workspace_key>`
- **THEN** the system resolves the key to its workspace context and injects `{org_id, workspace_id, scope: "workspace"}` into the auth context

#### Scenario: Valid system key
- **WHEN** a request arrives with a valid system-scope key
- **THEN** the system injects `{scope: "system", org_id: null, workspace_id: null}` into the auth context

#### Scenario: Invalid or revoked key
- **WHEN** a request arrives with a key whose hash matches no active record
- **THEN** the system returns `401 Unauthorized`

#### Scenario: Expired key
- **WHEN** a request arrives with a key where `expires_at` is in the past
- **THEN** the system returns `401 Unauthorized` with detail "API key expired"

### Requirement: API key rotation
The system SHALL support API key rotation by creating a replacement key and immediately revoking the old key.

#### Scenario: Rotate API key
- **WHEN** a user sends POST `/api/api-keys/{id}/rotate`
- **THEN** the system creates a new key with the same scope and bindings, sets the old key `is_active = false`, returns `200` with the new raw key. The old key is immediately invalid.

### Requirement: API key revocation
The system SHALL support explicit API key revocation (soft delete). Revoked keys are no longer valid for authentication.

#### Scenario: Revoke API key
- **WHEN** a user sends DELETE `/api/api-keys/{id}`
- **THEN** the system sets `is_active = false` on the key, returns `204`

#### Scenario: List API keys
- **WHEN** a user sends GET `/api/api-keys`
- **THEN** the system returns paginated list of API keys created by the user, showing `{id, name, key_prefix, scope, is_active, last_used_at, expires_at, created_at}`

### Requirement: Environment variable API key deprecation
The system SHALL continue to support the `HECATE_API_KEYS` environment variable during a deprecation period, logging a warning on each use. Env-var keys are treated as system-scope keys.

#### Scenario: Env-var key still works with warning
- **WHEN** a request authenticates with a key from `HECATE_API_KEYS` env var
- **THEN** the system accepts the key, logs a deprecation warning, and treats it as system-scope

#### Scenario: DB key takes precedence
- **WHEN** a key exists in both env var and database
- **THEN** the system resolves the DB record first, ignoring the env var match
