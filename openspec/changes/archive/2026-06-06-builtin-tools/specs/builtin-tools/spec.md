## ADDED Requirements — 新增需求

### Requirement: web_search 工具通过可配置的提供商搜索网络 — web_search tool searches the web via configurable provider
系统应提供一个 `web_search` 内置工具，接受 `query` 字符串和可选的 `max_results` 整数，调用配置的搜索提供商（Tavily/Serper/DuckDuckGo），并返回搜索结果列表。

#### Scenario: 使用默认提供商搜索 — Search with default provider
- **当** 调用 `web_search({"query": "Python async"})` 且未设置 `SEARCH_PROVIDER`
- **则** 工具应使用 DuckDuckGo 作为默认提供商并返回搜索结果

#### Scenario: 使用 Tavily 提供商搜索 — Search with Tavily provider
- **当** 调用 `web_search({"query": "Python async"})` 且设置了 `SEARCH_PROVIDER=tavily` 和 `SEARCH_API_KEY`
- **则** 工具应调用 Tavily API 并返回结构化的搜索结果

#### Scenario: 密钥必需提供商缺少 API 密钥 — Missing API key for key-required provider
- **当** 使用 `SEARCH_PROVIDER=tavily` 调用 `web_search` 但未设置 `SEARCH_API_KEY`
- **则** 工具应抛出 `ValueError` 指示需要 API 密钥

#### Scenario: 搜索结果格式 — Search results format
- **当** `web_search` 返回结果
- **则** 每个结果至少应包含：`title`（str）、`url`（str）、`snippet`（str）

### Requirement: read_file 工具在工作区内读取文件内容 — read_file tool reads file contents within workspace
系统应提供一个 `read_file` 内置工具，接受 `path` 字符串（相对于工作区根目录）并返回文件内容作为字符串。

#### Scenario: 读取现有文件 — Read existing file
- **当** 调用 `read_file({"path": "data/report.txt"})` 且文件存在于 `WORKSPACE_ROOT` 内
- **则** 工具应返回文件内容作为字符串

#### Scenario: 读取不存在的文件 — Read non-existent file
- **当** 调用 `read_file({"path": "nonexistent.txt"})` 且文件不存在
- **则** 工具应抛出 `FileNotFoundError`

#### Scenario: 路径遍历防护 — Path traversal prevention
- **当** 调用 `read_file({"path": "../../etc/passwd"})`
- **则** 工具应抛出 `ValueError` 指示路径在工作区之外

### Requirement: write_file 工具在工作区内将内容写入文件 — write_file tool writes content to files within workspace
系统应提供一个 `write_file` 内置工具，接受 `path` 字符串和 `content` 字符串，如需要则创建父目录，并将内容写入文件。

#### Scenario: 写入新文件 — Write new file
- **当** 调用 `write_file({"path": "output/result.txt", "content": "hello"})`
- **则** 工具应创建文件（及父目录）并返回成功消息

#### Scenario: 覆盖现有文件 — Overwrite existing file
- **当** 调用 `write_file({"path": "data.txt", "content": "updated"})` 且文件已存在
- **则** 工具应覆盖文件内容

#### Scenario: 路径遍历防护 — Path traversal prevention
- **当** 使用绝对路径调用 `write_file({"path": "/tmp/malicious", "content": "..."})`
- **则** 工具应抛出 `ValueError` 指示路径在工作区之外

### Requirement: list_files 工具列出工作区内的目录内容 — list_files tool lists directory contents within workspace
系统应提供一个 `list_files` 内置工具，接受可选的 `path` 字符串（默认为工作区根目录）并返回文件和目录名称列表。

#### Scenario: 列出根目录 — List root directory
- **当** 调用 `list_files({})`
- **则** 工具应返回工作区根目录的内容

#### Scenario: 列出子目录 — List subdirectory
- **当** 调用 `list_files({"path": "data"})` 且目录存在
- **则** 工具应返回 `WORKSPACE_ROOT/data/` 的内容

#### Scenario: 列出不存在的目录 — List non-existent directory
- **当** 调用 `list_files({"path": "nonexistent"})`
- **则** 工具应抛出 `FileNotFoundError`

### Requirement: execute_code 工具在 Docker 沙箱中运行 Python 代码 — execute_code tool runs Python code in Docker sandbox
系统应提供一个 `execute_code` 内置工具，接受 `code` 字符串，在 Docker 容器内通过 `SandboxExecutor` 执行它，并返回 stdout、stderr 和退出码。

#### Scenario: 成功的代码执行 — Successful code execution
- **当** 调用 `execute_code({"code": "print(2 + 2)"})`
- **则** 工具应返回一个字典，包含 `stdout: "4\n"`、`stderr: ""`、`exit_code: 0`

#### Scenario: 有错误的代码 — Code with error
- **当** 调用 `execute_code({"code": "1/0"})`
- **则** 工具应返回一个字典，包含非零的 `exit_code` 和包含错误回溯的 `stderr`

#### Scenario: 代码超时 — Code timeout
- **当** 调用 `execute_code({"code": "import time; time.sleep(60)"})` 且超时为 30 秒
- **则** 工具应返回一个字典，包含 `timed_out: True`

#### Scenario: Docker 不可用 — Docker not available
- **当** 调用 `execute_code` 但 Docker 守护进程未运行
- **则** 工具应返回指示沙箱不可用的错误消息，而不崩溃
