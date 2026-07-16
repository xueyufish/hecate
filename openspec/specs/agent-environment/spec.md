# agent-environment Specification

## Purpose
TBD - created by archiving change agent-environment. Update Purpose after archive.
## Requirements
### Requirement: AgentEnvironment abstraction
The system SHALL provide an `AgentEnvironment` ABC that represents the agent's persistent execution environment. Each environment is scoped to a single agent and contains subdirectories for sessions, files, memory, and skills.

#### Scenario: Environment has required subdirectories
- **WHEN** an agent environment is created
- **THEN** the environment contains `sessions/`, `files/`, `memory/`, and `skills/` subdirectories

#### Scenario: Environment is scoped to agent
- **WHEN** an environment is accessed for agent A
- **THEN** agent A cannot access agent B's environment files

### Requirement: LocalEnvironment filesystem implementation
The system SHALL provide a `LocalEnvironment` implementation that stores agent data on the local filesystem at `{WORKSPACE_ROOT}/{agent_id}/`.

#### Scenario: File write and read
- **WHEN** a file is written to `files/report.txt` in the environment
- **THEN** the file can be read back with the same content

#### Scenario: File listing
- **WHEN** files exist in the `files/` subdirectory
- **THEN** `list_files("files/")` returns the file list with metadata

#### Scenario: File deletion
- **WHEN** a file is deleted from the environment
- **THEN** subsequent `exists()` returns False

### Requirement: EnvironmentManager lifecycle
The system SHALL provide an `EnvironmentManager` that manages environment lifecycle with lazy creation and TTL-based eviction.

#### Scenario: Lazy creation on first use
- **WHEN** `get_environment(agent_id)` is called for an agent with no existing environment
- **THEN** a new environment is created and returned

#### Scenario: Cached environment reuse
- **WHEN** `get_environment(agent_id)` is called twice for the same agent
- **THEN** the same environment instance is returned (cached)

#### Scenario: TTL eviction
- **WHEN** an environment has been idle for longer than the configured TTL
- **THEN** the environment is closed and removed from cache on next access

#### Scenario: TTL reset on interaction
- **WHEN** a file operation is performed on an environment
- **THEN** the environment's TTL timer is reset

#### Scenario: Close all environments
- **WHEN** `close_all()` is called (e.g., on application shutdown)
- **THEN** all cached environments are closed

### Requirement: REST API for file management
The system SHALL expose REST API endpoints for managing files in an agent's environment.

#### Scenario: List files
- **WHEN** a client requests `GET /api/agents/{agent_id}/environment/files`
- **THEN** the system returns the file list in the `files/` subdirectory

#### Scenario: Read file
- **WHEN** a client requests `GET /api/agents/{agent_id}/environment/files/{path}`
- **THEN** the system returns the file content

#### Scenario: Write file
- **WHEN** a client requests `POST /api/agents/{agent_id}/environment/files` with file content
- **THEN** the file is written to the `files/` subdirectory

#### Scenario: Delete file
- **WHEN** a client requests `DELETE /api/agents/{agent_id}/environment/files/{path}`
- **THEN** the file is removed from the environment

#### Scenario: Environment stats
- **WHEN** a client requests `GET /api/agents/{agent_id}/environment/stats`
- **THEN** the system returns file count, total size, and creation time

### Requirement: Session auto-association
The system SHALL automatically associate sessions with the agent's environment. No manual environment ID management is needed.

#### Scenario: Session gets environment context
- **WHEN** a session is created for an agent
- **THEN** the agent's environment info (root path) is available in the execution context

#### Scenario: Multiple sessions share environment
- **WHEN** two sessions are created for the same agent
- **THEN** both sessions access the same environment files

