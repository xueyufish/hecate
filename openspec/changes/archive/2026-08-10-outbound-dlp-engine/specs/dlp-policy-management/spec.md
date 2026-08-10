## ADDED Requirements

### Requirement: DLPPolicyModel data model
The system SHALL provide `DLPPolicyModel` ORM table (`dlp_policies`) with fields: `id` (UUID PK), `org_id` (UUID), `workspace_id` (UUID | None, null = org-level), `agent_id` (UUID | None, null = workspace/org-level), `entity_type` (str), `direction` (str), `action` (str, ALLOW/BLOCK/MASK/AUDIT), `mask_format` (str | None), `is_locked` (bool), `enabled` (bool), `created_at`, `updated_at`, `deleted_at`.

#### Scenario: Create policy
- **WHEN** `DLPPolicyModel(org_id=X, entity_type="EMAIL", direction="llm_output", action="MASK")` is saved
- **THEN** the row SHALL be persisted to `dlp_policies` table

#### Scenario: Policy scoped to workspace
- **WHEN** `workspace_id` is set
- **THEN** the policy SHALL only apply within that workspace

#### Scenario: Soft delete
- **WHEN** `deleted_at` is set
- **THEN** the policy SHALL be excluded from queries but the row SHALL be preserved

### Requirement: DLPCustomRegexModel data model
The system SHALL provide `DLPCustomRegexModel` ORM table (`dlp_custom_regex`) for user-defined regex patterns, with fields: `id` (UUID PK), `org_id` (UUID), `workspace_id` (UUID | None), `entity_type` (str), `pattern` (str), `description` (str | None), `validation` (str | None, e.g., "luhn"), `enabled` (bool).

#### Scenario: Create custom regex
- **WHEN** a user creates `DLPCustomRegexModel(entity_type="EMPLOYEE_ID", pattern="EMP-\d{6}")`
- **THEN** the row SHALL be persisted and used by RegistryFactory

### Requirement: DLPDictionaryModel data model
The system SHALL provide `DLPDictionaryModel` ORM table (`dlp_dictionaries`) for user-defined dictionaries, with fields: `id` (UUID PK), `org_id` (UUID), `workspace_id` (UUID | None), `name` (str), `entity_type` (str), `terms` (JSON list of strings), `case_sensitive` (bool), `enabled` (bool).

#### Scenario: Create dictionary
- **WHEN** a user creates `DLPDictionaryModel(name="projects", entity_type="INTERNAL_PROJECT", terms=["Alpha", "Beta"])`
- **THEN** the row SHALL be persisted and used by RegistryFactory

### Requirement: DLPPolicyResolver three-level resolution
The system SHALL define `DLPPolicyResolver.resolve(entity_type, direction, org_id, workspace_id, agent_id) -> DLPAction` that returns the most specific matching rule's action, with `is_locked` enforcing that locked rules at higher scopes cannot be overridden by lower scopes.

#### Scenario: Agent overrides org
- **WHEN** agent rule `EMAIL→ALLOW` and org rule `EMAIL→MASK(is_locked=False)` both match
- **THEN** `resolve()` SHALL return `ALLOW` (agent wins)

#### Scenario: Locked rule cannot be overridden
- **WHEN** agent rule `AWS_KEY→ALLOW` and org rule `AWS_KEY→BLOCK(is_locked=True)` both match
- **THEN** `resolve()` SHALL return `BLOCK` (is_locked prevents override)

#### Scenario: Most specific wins across scopes
- **WHEN** scope order is agent > workspace > org
- **THEN** `resolve()` SHALL search agent first, then workspace, then org, then default

#### Scenario: Wildcard matching
- **WHEN** org rule has `entity_type="*", direction="*", action=BLOCK`
- **THEN** it SHALL match any entity_type and direction (catch-all)

#### Scenario: No rule found
- **WHEN** no matching rule at any scope
- **THEN** `resolve()` SHALL return `DLPAction.ALLOW` (fail-open default)

### Requirement: Built-in default rules
The system SHALL create the following default policies on first deploy for each new organization: secrets (AWS_ACCESS_KEY, GCP_SERVICE_KEY, PRIVATE_KEY, JWT_TOKEN, GITHUB_TOKEN) → BLOCK with `is_locked=True`; PII (SSN, CREDIT_CARD, CHINA_ID_CARD) → MASK; context-dependent (EMAIL, PHONE, IP_ADDRESS) → AUDIT.

#### Scenario: Default rules created
- **WHEN** a new organization is created
- **THEN** the default rules SHALL be inserted into `dlp_policies` for that org_id

#### Scenario: Default rules are idempotent
- **WHEN** the deploy runs twice
- **THEN** the second run SHALL NOT create duplicate default rules

### Requirement: DLP REST API for policy management
The system SHALL expose REST endpoints under `/api/v1/dlp/` for policy CRUD, custom regex CRUD, dictionary CRUD, and test dry-run.

#### Scenario: Create policy
- **WHEN** `POST /api/v1/dlp/policies` with `{entity_type, direction, action}` payload
- **THEN** the system creates a DLPPolicyModel and returns 201 with the created policy

#### Scenario: Update policy
- **WHEN** `PUT /api/v1/dlp/policies/{id}` with new action
- **THEN** the system updates the policy and returns 200

#### Scenario: Delete policy (soft)
- **WHEN** `DELETE /api/v1/dlp/policies/{id}`
- **THEN** the system sets `deleted_at` and returns 204

#### Scenario: Test dry-run
- **WHEN** `POST /api/v1/dlp/scan/test` with `{text, direction, org_id, workspace_id, agent_id}`
- **THEN** the system returns the DLPScanner result without persisting findings

#### Scenario: List entities
- **WHEN** `GET /api/v1/dlp/entities`
- **THEN** the system returns all known entity types (from RecognizerRegistry + custom entries)