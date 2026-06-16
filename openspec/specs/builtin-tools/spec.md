## ADDED Requirements

### Requirement: web_search tool searches the web via configurable provider
The system SHALL provide a `web_search` built-in tool that accepts a `query` string and optional `max_results` integer, calls the configured search provider (Tavily / Serper / DuckDuckGo), and returns a list of search results.

#### Scenario: Search with default provider
- **WHEN** `web_search({"query": "Python async"})` is called and `SEARCH_PROVIDER` is not set
- **THEN** the tool SHALL use DuckDuckGo as the default provider and return search results

#### Scenario: Search with Tavily provider
- **WHEN** `web_search({"query": "Python async"})` is called and `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY` is set
- **THEN** the tool SHALL call the Tavily API and return structured search results

#### Scenario: Missing API key for key-required provider
- **WHEN** `web_search` is called with `SEARCH_PROVIDER=tavily` but `SEARCH_API_KEY` is not set
- **THEN** the tool SHALL raise a `ValueError` indicating the API key is required

#### Scenario: Search results format
- **WHEN** `web_search` returns results
- **THEN** each result SHALL contain at minimum: `title` (str), `url` (str), `snippet` (str)

### Requirement: read_file tool reads file contents within workspace
The system SHALL provide a `read_file` built-in tool that accepts a `path` string (relative to workspace root) and returns the file contents as a string.

#### Scenario: Read existing file
- **WHEN** `read_file({"path": "data/report.txt"})` is called and the file exists within `WORKSPACE_ROOT`
- **THEN** the tool SHALL return the file contents as a string

#### Scenario: Read non-existent file
- **WHEN** `read_file({"path": "nonexistent.txt"})` is called and the file does not exist
- **THEN** the tool SHALL raise `FileNotFoundError`

#### Scenario: Path traversal prevention
- **WHEN** `read_file({"path": "../../etc/passwd"})` is called
- **THEN** the tool SHALL raise `ValueError` indicating the path is outside the workspace

### Requirement: write_file tool writes content to files within workspace
The system SHALL provide a `write_file` built-in tool that accepts a `path` string and `content` string, creates parent directories if needed, and writes the content to the file.

#### Scenario: Write new file
- **WHEN** `write_file({"path": "output/result.txt", "content": "hello"})` is called
- **THEN** the tool SHALL create the file (and parent directories) and return a success message

#### Scenario: Overwrite existing file
- **WHEN** `write_file({"path": "data.txt", "content": "updated"})` is called and the file already exists
- **THEN** the tool SHALL overwrite the file content

#### Scenario: Path traversal prevention
- **WHEN** `write_file({"path": "/tmp/malicious", "content": "..."})` is called with an absolute path
- **THEN** the tool SHALL raise `ValueError` indicating the path is outside the workspace

### Requirement: list_files tool lists directory contents within workspace
The system SHALL provide a `list_files` built-in tool that accepts an optional `path` string (defaults to workspace root) and returns a list of file and directory names.

#### Scenario: List root directory
- **WHEN** `list_files({})` is called
- **THEN** the tool SHALL return the contents of the workspace root directory

#### Scenario: List subdirectory
- **WHEN** `list_files({"path": "data"})` is called and the directory exists
- **THEN** the tool SHALL return the contents of `WORKSPACE_ROOT/data/`

#### Scenario: List non-existent directory
- **WHEN** `list_files({"path": "nonexistent"})` is called
- **THEN** the tool SHALL raise `FileNotFoundError`

### Requirement: execute_code tool runs Python code in Docker sandbox
The system SHALL provide an `execute_code` built-in tool that accepts a `code` string, executes it inside a Docker container via `SandboxExecutor`, and returns stdout, stderr, and exit code.

#### Scenario: Successful code execution
- **WHEN** `execute_code({"code": "print(2 + 2)"})` is called
- **THEN** the tool SHALL return a dict with `stdout: "4\n"`, `stderr: ""`, `exit_code: 0`

#### Scenario: Code with error
- **WHEN** `execute_code({"code": "1/0"})` is called
- **THEN** the tool SHALL return a dict with `exit_code` non-zero and `stderr` containing the error traceback

#### Scenario: Code timeout
- **WHEN** `execute_code({"code": "import time; time.sleep(60)"})` is called and the timeout is 30 seconds
- **THEN** the tool SHALL return a dict with `timed_out: True`

#### Scenario: Docker not available
- **WHEN** `execute_code` is called but the Docker daemon is not running
- **THEN** the tool SHALL return an error message indicating sandbox is unavailable, without crashing
