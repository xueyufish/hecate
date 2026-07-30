## ADDED Requirements — 新增需求

### Requirement: CLI 入口点和命令结构 — CLI 入口点和命令结构
系统 SHALL 提供一个 `hecate` CLI 命令，注册为 pyproject.toml 中的 `console_scripts` 入口点，使用 `typer` 框架，嵌套子命令组映射到 API 资源域。

#### Scenario: CLI 帮助显示所有资源组
- **WHEN** 执行 `hecate --help`
- **THEN** 输出 SHALL 列出子命令组：agent、session、chat、kb、tool、skill、workflow、prompt、memory、template、conversation、model、config、auth

#### Scenario: 资源组帮助显示操作
- **WHEN** 执行 `hecate agent --help`
- **THEN** 输出 SHALL 列出操作：list、create、get、update、delete

#### Scenario: 未知命令返回错误
- **WHEN** 执行 `hecate nonexistent --help`
- **THEN** CLI SHALL 以非零代码退出并显示错误消息

### Requirement: 通过 TOML 配置文件的配置管理
系统 SHALL 从 `~/.hecate/config.toml` 读写 CLI 配置，支持带 `base_url`、`api_key` 和 `output` 设置的命名配置文件。当未提供 `--profile` 标志时，SHALL 使用 `default` 配置文件。

#### Scenario: 默认配置
- **WHEN** `~/.hecate/config.toml` 不存在且未设置环境变量
- **THEN** CLI SHALL 使用 `base_url=http://localhost:8000` 并在第一个需要认证的命令时提示输入 `api_key`

#### Scenario: 设置配置值
- **WHEN** 执行 `hecate config set api_key hec-xxxxx`
- **THEN** CLI SHALL 将值写入 `~/.hecate/config.toml` 的活动配置文件

#### Scenario: 使用命名配置文件
- **WHEN** 执行 `hecate --profile staging agent list`
- **THEN** CLI SHALL 从配置中读取 `staging` 配置文件的 `base_url` 和 `api_key`

#### Scenario: 显示当前配置
- **WHEN** 执行 `hecate config show`
- **THEN** CLI SHALL 显示活动配置文件的设置，`api_key` 被屏蔽

### Requirement: 双重认证 — API Key 和 JWT
CLI SHALL 支持通过直接 API 密钥存储和 JWT 登录（带自动 token 刷新）进行认证。

#### Scenario: 使用存储的 API Key 认证
- **WHEN** 执行命令且活动配置文件已设置 `api_key`
- **THEN** CLI SHALL 在每次 API 请求中发送 `Authorization: Bearer <api_key>` 标头

#### Scenario: 使用 JWT 登录
- **WHEN** 使用正确凭据执行 `hecate auth login --email user@example.com`
- **THEN** CLI SHALL 调用 `POST /api/auth/login`，将 access token 和 refresh token 存储在配置文件中，并为后续请求使用 access token

#### Scenario: JWT 过期时自动刷新
- **WHEN** API 请求返回 401 且配置文件中存在 refresh token
- **THEN** CLI SHALL 使用 refresh token 调用 `POST /api/auth/refresh`，更新存储的 access token，并重试原始请求

#### Scenario: Whoami 显示当前用户
- **WHEN** 执行 `hecate auth whoami`
- **THEN** CLI SHALL 调用 `GET /api/auth/me` 并显示当前用户的电子邮件和 ID

### Requirement: 输出格式 — 表格和 JSON
CLI SHALL 默认以 rich 表格显示输出，并支持 `--json` 标志用于机器可读的 JSON 输出。

#### Scenario: 默认表格输出
- **WHEN** 不带 `--json` 执行 `hecate agent list`
- **THEN** CLI SHALL 渲染包含 id、name、mode 和 model_config 列的 rich 表格

#### Scenario: JSON 输出
- **WHEN** 执行 `hecate agent list --json`
- **THEN** CLI SHALL 将原始 JSON 响应打印到 stdout，用于通过 jq 或其他工具的管道处理

### Requirement: HTTP 客户端包装器
CLI SHALL 对所有 API 通信使用 `httpx` 同步客户端，具有可配置的超时和自动错误处理。

#### Scenario: 成功的 API 调用
- **WHEN** CLI 命令向 `/api/agents` 发出 GET 请求
- **THEN** 客户端 SHALL 返回解析后的 JSON 响应

#### Scenario: 带用户友好消息的 API 错误
- **WHEN** API 返回 4xx 或 5xx 错误
- **THEN** CLI SHALL 显示来自 API 响应的错误代码和消息，并以非零代码退出

#### Scenario: 连接被拒绝
- **WHEN** API 服务器不可达
- **THEN** CLI SHALL 显示 "Error: Cannot connect to Hecate server at <base_url>" 并以代码 1 退出

### Requirement: Agent CRUD 命令
CLI SHALL 为 agent 生命周期管理提供 `hecate agent` 子命令。

#### Scenario: 列出 agents
- **WHEN** 执行 `hecate agent list`
- **THEN** CLI SHALL 调用 `GET /api/agents` 并在表格中显示 agents

#### Scenario: 创建 agent
- **WHEN** 执行 `hecate agent create --name "Test" --model gpt-4o --mode chat`
- **THEN** CLI SHALL 使用提供的参数调用 `POST /api/agents` 并显示创建的 agent

#### Scenario: 获取 agent
- **WHEN** 执行 `hecate agent get <agent_id>`
- **THEN** CLI SHALL 调用 `GET /api/agents/{id}` 并显示 agent 详情

#### Scenario: 更新 agent
- **WHEN** 执行 `hecate agent update <agent_id> --name "New Name"`
- **THEN** CLI SHALL 使用更新的字段调用 `PUT /api/agents/{id}`

#### Scenario: 删除 agent
- **WHEN** 执行 `hecate agent delete <agent_id>`
- **THEN** CLI SHALL 提示确认并调用 `DELETE /api/agents/{id}`

### Requirement: Session 管理命令
CLI SHALL 为 session 生命周期提供 `hecate session` 子命令。

#### Scenario: 创建 session
- **WHEN** 执行 `hecate session create --agent-id <id>`
- **THEN** CLI SHALL 调用 `POST /api/sessions` 并返回 session ID

#### Scenario: 列出 sessions
- **WHEN** 执行 `hecate session list`
- **THEN** CLI SHALL 调用 `GET /api/sessions` 并在表格中显示 sessions

#### Scenario: 恢复中断的 session
- **WHEN** 执行 `hecate session resume <session_id> --message "approved"`
- **THEN** CLI SHALL 使用 resume 值调用 `POST /api/sessions/{id}/resume`

### Requirement: 带流式的聊天命令
CLI SHALL 提供 `hecate chat send` 用于一次性消息和 `hecate chat interactive` 用于带 SSE 流式的交互式 REPL 会话。

#### Scenario: 一次性聊天消息
- **WHEN** 执行 `hecate chat send <agent_id> "Hello"`
- **THEN** CLI SHALL 使用消息调用 `POST /v1/chat/completions` 并显示助手的响应

#### Scenario: 带流式的交互式聊天
- **WHEN** 执行 `hecate chat interactive <agent_id>`
- **THEN** CLI SHALL 打开一个交互式 REPL，使用 `stream=true` 向 agent 发送消息，增量显示到达的响应 token

#### Scenario: 交互式聊天斜杠命令
- **WHEN** 用户在交互模式下输入 `/clear`、`/exit` 或 `/history`
- **THEN** CLI SHALL 相应地处理斜杠命令（清除上下文、退出、显示会话历史）

#### Scenario: 流式 SSE 解析
- **WHEN** API 在流式期间返回 SSE 事件
- **THEN** CLI SHALL 解析 `data: {...}` 行，提取 `choices[0].delta.content`，并打印每个 token 而不换行

### Requirement: 知识库命令
CLI SHALL 为知识库和文档管理提供 `hecate kb` 子命令。

#### Scenario: 列出知识库
- **WHEN** 执行 `hecate kb list`
- **THEN** CLI SHALL 调用 `GET /api/knowledge-bases` 并在表格中显示知识库

#### Scenario: 创建知识库
- **WHEN** 执行 `hecate kb create --name "My KB" --description "Test"`
- **THEN** CLI SHALL 调用 `POST /api/knowledge-bases` 并返回创建的知识库

#### Scenario: 向知识库上传文档
- **WHEN** 执行 `hecate kb upload <kb_id> document.pdf`
- **THEN** CLI SHALL 使用 multipart 文件上传调用 `POST /api/knowledge-bases/{id}/documents`

#### Scenario: 列出知识库中的文档
- **WHEN** 执行 `hecate kb documents <kb_id>`
- **THEN** CLI SHALL 调用 `GET /api/knowledge-bases/{id}/documents` 并显示带解析状态的文档

### Requirement: Tool 命令
CLI SHALL 为工具列出提供 `hecate tool` 子命令。

#### Scenario: 列出工具
- **WHEN** 执行 `hecate tool list`
- **THEN** CLI SHALL 调用 `GET /api/tools` 并在表格中显示工具

#### Scenario: 按源过滤列出工具
- **WHEN** 执行 `hecate tool list --source builtin`
- **THEN** CLI SHALL 调用 `GET /api/tools?source=builtin` 并仅显示内置工具

### Requirement: Skill CRUD 命令
CLI SHALL 为技能管理提供 `hecate skill` 子命令。

#### Scenario: 列出技能
- **WHEN** 执行 `hecate skill list`
- **THEN** CLI SHALL 调用 `GET /api/skills` 并在表格中显示技能

#### Scenario: 从 SKILL.md 导入技能
- **WHEN** 执行 `hecate skill import skill.md`
- **THEN** CLI SHALL 使用文件上传调用 `POST /api/skills/import`

### Requirement: Workflow 命令
CLI SHALL 为工作流 CRUD、版本管理、验证和测试运行提供 `hecate workflow` 子命令。

#### Scenario: 列出工作流
- **WHEN** 执行 `hecate workflow list`
- **THEN** CLI SHALL 调用 `GET /api/workflows` 并在表格中显示工作流

#### Scenario: 验证工作流
- **WHEN** 执行 `hecate workflow validate <workflow_id>`
- **THEN** CLI SHALL 调用 `POST /api/workflows/{id}/validate` 并显示验证结果

#### Scenario: 测试运行工作流
- **WHEN** 执行 `hecate workflow test-run <workflow_id>`
- **THEN** CLI SHALL 调用 `POST /api/workflows/{id}/test-run` 并显示执行结果

### Requirement: Prompt 命令
CLI SHALL 为 prompt CRUD 和版本管理提供 `hecate prompt` 子命令。

#### Scenario: 列出 prompts
- **WHEN** 执行 `hecate prompt list`
- **THEN** CLI SHALL 调用 `GET /api/prompts` 并在表格中显示 prompts

#### Scenario: 按标签获取 prompt
- **WHEN** 执行 `hecate prompt by-label production`
- **THEN** CLI SHALL 调用 `GET /api/prompts/by-label/production` 并显示 prompt 内容

### Requirement: Memory 命令
CLI SHALL 为记忆块和用户记忆提供 `hecate memory` 子命令。

#### Scenario: 列出 agent 记忆块
- **WHEN** 执行 `hecate memory blocks <agent_id>`
- **THEN** CLI SHALL 调用 `GET /api/agents/{id}/memory-blocks` 并在表格中显示块

#### Scenario: 搜索用户记忆
- **WHEN** 执行 `hecate memory search <query>`
- **THEN** CLI SHALL 调用 `GET /api/memory?q=<query>` 并显示匹配的记忆

### Requirement: Template 命令
CLI SHALL 为 agent 和编排模板提供 `hecate template` 子命令。

#### Scenario: 列出 agent 模板
- **WHEN** 执行 `hecate template agents`
- **THEN** CLI SHALL 调用 `GET /api/agent-templates` 并显示可用模板

#### Scenario: 实例化 agent 模板
- **WHEN** 执行 `hecate template agents instantiate <template_id> --name "My Agent"`
- **THEN** CLI SHALL 调用 `POST /api/agent-templates/{id}/instantiate` 并返回创建的 agent

### Requirement: Conversation 命令
CLI SHALL 为会话管理提供 `hecate conversation` 子命令。

#### Scenario: 列出会话
- **WHEN** 执行 `hecate conversation list`
- **THEN** CLI SHALL 调用 `GET /api/conversations` 并在表格中显示会话

#### Scenario: 获取带消息的会话
- **WHEN** 执行 `hecate conversation get <conversation_id>`
- **THEN** CLI SHALL 调用 `GET /api/conversations/{id}` 并显示包含所有消息的会话

### Requirement: Model provider 命令
CLI SHALL 为模型列出和提供者管理提供 `hecate model` 子命令。

#### Scenario: 列出可用模型
- **WHEN** 执行 `hecate model list`
- **THEN** CLI SHALL 调用 `GET /v1/models` 并在表格中显示模型

#### Scenario: 测试模型提供者连接
- **WHEN** 执行 `hecate model providers test <provider_id>`
- **THEN** CLI SHALL 调用 `POST /api/model-providers/{id}/test` 并显示测试结果

### Requirement: 分页支持
CLI SHALL 为所有列表命令支持 `--page` 和 `--page-size` 标志，默认为 page=1 和 page_size=20。

#### Scenario: 自定义分页
- **WHEN** 执行 `hecate agent list --page 2 --page-size 10`
- **THEN** CLI SHALL 调用 `GET /api/agents?page=2&page_size=10` 并显示结果

### Requirement: CLI 依赖
CLI SHALL 将 `typer>=0.15.0` 和 `rich>=13.0.0` 添加到 pyproject.toml 的主要依赖中，并注册指向 `hecate.cli.main:app` 的 `hecate` console_scripts 入口点。

#### Scenario: 安装并运行 CLI
- **WHEN** 执行 `uv pip install -e .`
- **THEN** `hecate` 命令 SHALL 在 shell 中可用
