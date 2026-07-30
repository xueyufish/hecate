## Context — 背景

Hecate 暴露了 68 个 REST API 端点（16 个路由器，12 个资源域）但没有 CLI。所有交互都需要直接 HTTP 调用。该平台需要开发者友好的 CLI，用于 agent 生命周期管理、交互式聊天、知识库操作以及所有资源的完整 CRUD。

当前状态：
- 所有端点使用 Bearer token 认证（`verify_api_key` — 接受 API Key 或 JWT）
- 响应遵循一致的 JSON 格式（列表为 `{"items": [...], "total": N}`）
- 聊天端点支持 SSE 流式（`POST /v1/chat/completions` 带 `stream=true`）
- `httpx` 已经是依赖项（在测试和 MCP 客户端中使用）
- pyproject.toml 中不存在 `[project.scripts]` 入口点

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 提供覆盖所有 68 个 API 端点的 `hecate` CLI
- 支持 SSE 流式的交互式聊天
- 带命名配置文件的配置管理
- 双重认证（API Key + JWT 自动刷新）
- 人类可读的表格输出（默认）+ `--json` 标志
- 对现有服务端代码零变更

**非目标：**
- 独立的 `hecate-cli` 包（CLI 随主包提供）
- 超越基本交互式聊天的 GUI / TUI
- 离线模式或本地 agent 执行
- Shell 补全脚本（稍后可通过 typer 内置支持添加）
- CLI 扩展的插件系统

## Decisions — 设计决策

### D1: 框架 — typer + rich

**选择**：typer（以 rich 作为 typer 的默认渲染器）

**理由**：
- typer 是类型注解驱动的——与 Hecate 的 FastAPI + Pydantic + SQLAlchemy 2.0 风格一致
- Langflow、Prefect Cloud 和 AutoGen Studio 都使用 typer 作为其 CLI
- typer 底层是 click，需要时可以混用 click 原生 API
- 样板代码最少 — 68 个端点需要大量子命令，`Annotated[type, Option()]` 比 click 装饰器简洁

**替代方案**：
- click: 更成熟但样板多，Dify/CrewAI/LiteLLM 使用
- cyclopts: Prefect 3.x 刚迁移过去，还不够成熟
- argparse: 样板极多，不适合 68 个端点

### D2: 命令结构 — 嵌套子命令

**选择**：`hecate <resource> <action>` 嵌套结构

```
hecate agent list
hecate agent create --name "My Agent" --model gpt-4o
hecate chat interactive <agent_id>
hecate kb upload <kb_id> document.pdf
```

**理由**：68 个端点不能扁平化。每个 API 路由器映射到一个 CLI 子命令组。

### D3: 配置 — 带配置文件的 TOML 文件

**选择**：`~/.hecate/config.toml` 带命名配置文件

```toml
[default]
base_url = "http://localhost:8000"
api_key = "hec-xxxxx"
output = "table"

[profiles.staging]
base_url = "https://staging.hecate.io"
api_key = "hec-yyyyy"
```

**理由**：TOML 是 Python 原生的（自 3.11 起 stdlib `tomllib`），支持多环境工作流的配置文件，并且是 Python 工具的标准（pyproject.toml、ruff、mypy）。

**替代方案**：
- 仅环境变量：不支持配置文件，多环境 UX 差
- JSON：可读性差，无注释
- YAML：需要额外依赖

### D4: 认证 — 双重模式

**选择**：支持 API Key（直接）和 JWT（登录 + 自动刷新）

- `hecate config set api_key hec-xxx` — 直接存储 API 密钥
- `hecate auth login --email user@example.com` — 获取 JWT，存储刷新 token，过期时自动刷新

**理由**：API Key 对服务账号最简单。JWT 是用户范围操作所需的（P3 多租户）。两种认证方法都已由 `verify_api_key` 支持。

### D5: HTTP 客户端 — httpx（同步）

**选择**：为所有 CLI→API 通信使用 `httpx` 同步客户端

**理由**：typer 命令是同步的。httpx 已经是依赖项。SSE 流式使用 `httpx.stream("POST", ...)` 逐行解析。CLI 上下文中不需要异步。

### D6: 输出格式 — rich 表格 + --json

**选择**：默认 rich 表格，`--json` 用于机器可读输出

**理由**：所有 AI 平台 CLI（LiteLLM、Langflow、Prefect Cloud）都使用 rich 输出。`--json` 支持通过 `jq` 管道或脚本处理。

### D7: 交互式聊天 — SSE 流式

**选择**：`hecate chat interactive <agent_id>` 打开带流式响应的 REPL

**实现**：
- 使用 `httpx.stream("POST", "/v1/chat/completions", json={..., "stream": True})`
- 逐行解析 SSE 事件（`data: {...}`）
- 通过 `rich.console.Console.print` 增量打印内容 token
- 支持斜杠命令：`/clear`、`/exit`、`/history`

**理由**：如果没有流式，交互式聊天需要等待 3-10 秒然后转储完整响应——用户体验不可用。

### D8: 模块结构

```
src/hecate/cli/
├── __init__.py
├── main.py              # 根 typer app，入口点
├── config.py            # 配置加载，配置文件管理
├── client.py            # HTTP 客户端包装器（httpx）
├── auth.py              # 登录，token 刷新，whoami
├── output.py            # 表格/JSON 格式化工具
├── commands/
│   ├── __init__.py
│   ├── agent.py         # hecate agent ...
│   ├── session.py       # hecate session ...
│   ├── chat.py          # hecate chat send/interactive
│   ├── kb.py            # hecate kb ...
│   ├── tool.py          # hecate tool ...
│   ├── skill.py         # hecate skill ...
│   ├── workflow.py      # hecate workflow ...
│   ├── prompt.py        # hecate prompt ...
│   ├── memory.py        # hecate memory ...
│   ├── template.py      # hecate template ...
│   ├── conversation.py  # hecate conversation ...
│   ├── model.py         # hecate model list / hecate model providers ...
│   └── message.py       # hecate message citations
```

## Risks / Trade-offs — 风险与权衡

- **[同步 vs 异步]** CLI 使用同步 httpx。对于并行 API 调用（例如列出多个资源），这是顺序的。可接受的权衡——CLI 是交互式的，而非批处理。
- **[Python 3.12 中的 TOML]** `tomllib` 是只读的。对于配置写入操作（`hecate config set`），我们需要手动序列化 TOML 或添加 `tomli_w` 依赖。缓解措施：实现最小的 TOML 写入器（配置是扁平键值对，不是复杂嵌套）。
- **[JWT 刷新竞争]** 如果 JWT 在会话期间过期，CLI 必须透明地刷新。缓解措施：在每个请求前检查过期时间，主动刷新。
- **[68 个命令 = 大型 CLI]** 每个资源域一个 CLI 模块使每个文件保持可管理（约 100-200 行）。模板模式（创建一个，复制到其他）确保一致性。
