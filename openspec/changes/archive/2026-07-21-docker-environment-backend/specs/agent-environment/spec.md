## ADDED Requirements

### Requirement: Shell command execution in AgentEnvironment
The `AgentEnvironment` ABC SHALL provide an `exec_shell(command, *, cwd, timeout) -> ExecResult` method for executing shell commands inside the environment. `ExecResult` SHALL contain `exit_code: int`, `stdout: bytes`, and `stderr: bytes`. All implementations (`LocalEnvironment`, `DockerEnvironment`) MUST implement this method.

#### Scenario: LocalEnvironment exec_shell runs on host
- **WHEN** `exec_shell(["echo", "hello"])` is called on a `LocalEnvironment`
- **THEN** the command runs via `asyncio.create_subprocess_exec` on the host
- **AND** the returned `ExecResult` has `exit_code=0`, `stdout=b"hello\n"`

#### Scenario: exec_shell with working directory
- **WHEN** `exec_shell(["ls"], cwd="files/")` is called
- **THEN** the command executes with the specified directory as its working directory

#### Scenario: exec_shell with timeout
- **WHEN** `exec_shell(["sleep", "10"], timeout=1.0)` is called
- **THEN** the command is terminated after 1 second
- **AND** the returned `ExecResult` has `exit_code=-1` and `stderr` containing a timeout message

#### Scenario: exec_shell captures stderr separately
- **WHEN** a command writes to stderr
- **THEN** `ExecResult.stderr` contains the stderr output and `ExecResult.stdout` contains only stdout

### Requirement: DockerEnvironment container backend
The system SHALL provide a `DockerEnvironment` implementation of `AgentEnvironment` that isolates an agent's files and processes inside a Docker container. Each agent SHALL have its own container with a named volume (`agent-{agent_id}`) mounted at `/env` containing subdirectories `sessions/`, `files/`, `memory/`, `skills/`.

#### Scenario: DockerEnvironment creates container on first access
- **WHEN** `EnvironmentManager.get_or_create(agent_id)` is called with `AGENT_ENV_BACKEND=docker`
- **THEN** a Docker container is created with image `DOCKER_AGENT_IMAGE`, volume `agent-{agent_id}` mounted at `/env`, and runtime `DOCKER_RUNTIME`
- **AND** the container's `/env` directory has `sessions/`, `files/`, `memory/`, `skills/` subdirectories

#### Scenario: DockerEnvironment reuses warm container
- **WHEN** a container for `agent_id` exists in the warm pool
- **THEN** `get_or_create(agent_id)` reuses that container instead of creating a new one
- **AND** the TTL timer is reset

#### Scenario: DockerEnvironment file write and read
- **WHEN** `write_file("files/report.txt", b"hello")` is called on a `DockerEnvironment`
- **THEN** the file is written inside the container at `/env/files/report.txt`
- **AND** `read_file("files/report.txt")` returns `b"hello"`

#### Scenario: DockerEnvironment exec_shell runs inside container
- **WHEN** `exec_shell(["pip", "install", "pandas"])` is called on a `DockerEnvironment`
- **THEN** the command runs inside the container via `docker exec`
- **AND** the returned `ExecResult` reflects the command's exit code and output from inside the container

#### Scenario: DockerEnvironment container isolation
- **WHEN** agent A's container is running
- **THEN** agent A cannot access agent B's volume or files
- **AND** agent A's processes are isolated from agent B's processes via container namespaces

#### Scenario: DockerEnvironment with gVisor runtime
- **WHEN** `DOCKER_RUNTIME=runsc` is configured
- **THEN** containers are created with the `runsc` runtime
- **AND** syscalls inside the container are intercepted by gVisor's user-space kernel

### Requirement: EnvironmentManager backend selection
The `EnvironmentManager` SHALL support selecting the environment backend via the `AGENT_ENV_BACKEND` config setting. Valid values are `"local"` (default) and `"docker"`.

#### Scenario: Default backend is local
- **WHEN** `AGENT_ENV_BACKEND` is not set
- **THEN** `EnvironmentManager` creates `LocalEnvironment` instances (existing behavior)

#### Scenario: Docker backend selection
- **WHEN** `AGENT_ENV_BACKEND=docker` is set
- **THEN** `EnvironmentManager` creates `DockerEnvironment` instances

#### Scenario: Invalid backend rejected at startup
- **WHEN** `AGENT_ENV_BACKEND` is set to an unrecognized value (e.g., `"e2b"`)
- **THEN** the system raises a `ValueError` at `EnvironmentManager` initialization

### Requirement: Warm pool for container reuse
The `EnvironmentManager` SHALL maintain a warm pool of idle Docker containers to reduce cold-start latency. When a container is closed, it moves to the warm pool instead of being destroyed. The warm pool has a configurable maximum size and idle timeout.

#### Scenario: Container moves to warm pool on close
- **WHEN** `close(agent_id)` is called
- **THEN** the container is stopped but not destroyed
- **AND** it is placed in the warm pool for potential reuse

#### Scenario: Warm pool reuse on re-access
- **WHEN** `get_or_create(agent_id)` is called after a close
- **AND** the container is still in the warm pool
- **THEN** the container is restarted and reused

#### Scenario: Warm pool eviction when full
- **WHEN** the warm pool is at maximum capacity
- **AND** a new container needs to be evicted
- **THEN** the oldest idle container is destroyed (its volume persists)

#### Scenario: Warm pool idle timeout
- **WHEN** a container has been idle in the warm pool longer than the configured timeout
- **THEN** the container is destroyed on the next sweep (its volume persists)

## MODIFIED Requirements

### Requirement: AgentEnvironment abstraction
The system SHALL provide an `AgentEnvironment` ABC that represents the agent's persistent execution environment. Each environment is scoped to a single agent and contains subdirectories for sessions, files, memory, and skills. The ABC SHALL include an `exec_shell(command, *, cwd, timeout) -> ExecResult` method for executing shell commands inside the environment.

#### Scenario: Environment has required subdirectories
- **WHEN** an agent environment is created
- **THEN** the environment contains `sessions/`, `files/`, `memory/`, and `skills/` subdirectories

#### Scenario: Environment is scoped to agent
- **WHEN** an environment is accessed for agent A
- **THEN** agent A cannot access agent B's environment files

#### Scenario: exec_shell is available on all implementations
- **WHEN** any `AgentEnvironment` implementation is used
- **THEN** `exec_shell(command)` is available and returns an `ExecResult`

### Requirement: LocalEnvironment filesystem implementation
The system SHALL provide a `LocalEnvironment` implementation that stores agent data on the local filesystem at `{WORKSPACE_ROOT}/{agent_id}/`. The `LocalEnvironment` SHALL implement `exec_shell` by running commands on the host via `asyncio.create_subprocess_exec`.

#### Scenario: File write and read
- **WHEN** a file is written to `files/report.txt` in the environment
- **THEN** the file can be read back with the same content

#### Scenario: File listing
- **WHEN** files exist in the `files/` subdirectory
- **THEN** `list_files("files/")` returns the file list with metadata

#### Scenario: File deletion
- **WHEN** a file is deleted from the environment
- **THEN** subsequent `exists()` returns False

#### Scenario: exec_shell runs on host
- **WHEN** `exec_shell(["whoami"])` is called
- **THEN** the command runs on the host and returns the host user

### Requirement: EnvironmentManager lifecycle
The system SHALL provide an `EnvironmentManager` that manages environment lifecycle with lazy creation, TTL-based eviction, and configurable backend selection (`AGENT_ENV_BACKEND`). When `AGENT_ENV_BACKEND=docker`, the manager SHALL maintain a warm pool of idle containers for reuse.

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

#### Scenario: Backend selection via config
- **WHEN** `AGENT_ENV_BACKEND=docker` is set
- **THEN** the manager creates `DockerEnvironment` instances instead of `LocalEnvironment`
