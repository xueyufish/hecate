## MODIFIED Requirements

### Requirement: BaseModel provides UUID primary key, timestamps, and soft delete
The abstract `BaseModel` SHALL provide `id` (UUID4), `created_at`, `updated_at`, `deleted` (bool), and `deleted_at` columns for all concrete ORM models. The `deleted` field represents the deletion state; the `deleted_at` field is an audit timestamp recording when deletion occurred.

#### Scenario: UUID primary key auto-generated
- **WHEN** a new model instance is created
- **THEN** `id` SHALL be auto-generated via `uuid.uuid4`

#### Scenario: Timestamps set by database server
- **WHEN** a row is inserted
- **THEN** `created_at` and `updated_at` SHALL be set by `server_default=func.now()`

#### Scenario: Updated_at refreshed on UPDATE
- **WHEN** a row is updated
- **THEN** `updated_at` SHALL be refreshed via `onupdate=func.now()`

#### Scenario: New row is not deleted by default
- **WHEN** a new model instance is created
- **THEN** `deleted` SHALL be `False` and `deleted_at` SHALL be `None`

#### Scenario: Soft delete sets both deleted and deleted_at
- **WHEN** a row is soft-deleted
- **THEN** `deleted` SHALL be set to `True` and `deleted_at` SHALL be set to the current timestamp

#### Scenario: Active rows queried by deleted field
- **WHEN** queries filter for active (non-deleted) rows
- **THEN** they SHALL use `WHERE deleted = false` (not `WHERE deleted_at IS NULL`)

#### Scenario: Unique composite indexes include deleted field
- **WHEN** a unique index enforces name uniqueness among active rows
- **THEN** the index SHALL be `Index("name", <columns...>, "deleted", "deleted_at", unique=True)` — fully portable across PostgreSQL, MySQL, and SQLite

#### Scenario: Non-unique filtered indexes include deleted field
- **WHEN** a non-unique index previously used `postgresql_where=deleted_at IS NULL`
- **THEN** the index SHALL be `Index("name", <columns...>, "deleted")` — composite index without dialect-specific kwargs
