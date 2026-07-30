## Why — 为什么

Hecate 的 Agent 执行数据分散在 4 个存储后端（PostgreSQL、MinIO、Qdrant、文件系统），没有统一的抽象。Agent 无法管理自己的文件，内存不是工作空间范围内的，也没有 Agent 执行环境的生命周期管理。对 14 个平台（Bedrock AgentCore、AgentScope、Dify、Claude Code、Google Gemini、Salesforce Agentforce、Palantir AIP、Huawei AgentArts）的研究表明，所有成熟平台都有统一的 Agent 执行环境抽象。此特性引入了 `AgentEnvironment` — Agent 的持久执行上下文 — 区别于 `WorkspaceModel`（多租户隔离边界）。

## What Changes — 变更内容

- **AgentEnvironment ABC**：统一的 Agent 执行环境抽象，包含文件管理、生命周期和会话关联。`AgentEnvironment`（执行上下文）在概念上区别于 `WorkspaceModel`（租户隔离边界）。
- **LocalEnvironment**：基于文件系统的实现，将 Agent 数据存储在 `{WORKSPACE_ROOT}/{agent_id}/`，包含子目录：`sessions/`、`files/`、`memory/`、`skills/`。
- **EnvironmentManager**：生命周期管理器，支持懒创建、基于 TTL 的驱逐（默认 24 小时，每次交互重置）和多租户缓存。
- **文件 CRUD API**：用于在 Agent 环境中列出、读取、写入和删除文件的 REST 端点。
- **会话自动关联**：会话通过 `agent_id` 自动关联到 Agent 的环境 — 无需手动管理环境 ID。
- **WorkflowExecutionService 集成**：环境在首次使用时懒创建，信息通过 `execution_context` 传递给 Worker。

**命名理由**："AgentEnvironment"（而非 "AgentWorkspace"）旨在避免与 `WorkspaceModel`（多租户隔离边界）的概念冲突。行业对比：Dify 使用 "Tenant" + "AgentRuntimeSession"，Bedrock 使用 "Tenant" + "Agent Runtime"，AgentScope 使用 "Workspace"（但其没有租户模型）。

## Capabilities — 能力

### 新能力

- `agent-environment`：AgentEnvironment ABC、LocalEnvironment、EnvironmentManager（TTL 驱逐）、文件 CRUD API、会话自动关联、WorkflowExecutionService 集成

### 修改的能力

- _(无 — 此为新增功能；现有的会话/对话系统保持不变)_

## Impact — 影响

- **新文件**：
  - `src/hecate/services/environment/__init__.py`
  - `src/hecate/services/environment/environment.py` — AgentEnvironment ABC + LocalEnvironment
  - `src/hecate/services/environment/manager.py` — EnvironmentManager
  - `src/hecate/api/management/environment.py` — REST API
  - `tests/test_services/test_environment/` — 测试
- **修改的文件**：
  - `src/hecate/core/config.py` — 新设置：`AGENT_ENV_TTL`、`AGENT_ENV_ENABLED`
  - `src/hecate/main.py` — 注册环境路由
- **依赖**：无新依赖
- **迁移**：无（MVP 中无 DB 模型变更）
