## Why — 为什么

Hecate 目前没有跨进程重启持久化每会话工作状态的机制。`WorkflowExecutionService` 中的 `execution_context` 字典是临时的 — 每次调用都会新创建，进程退出时丢失。这意味着对话缓冲区、压缩摘要、权限缓存以及工具/任务的子上下文无法在崩溃或缩容后存活。竞争平台（AgentScope、Claude Code、Bedrock AgentCore）都通过显式的 "AgentState" 概念解决了这个问题，该概念将易失的每会话状态与持久的每 Agent 环境分离开来。

此变更引入了 AgentState 抽象和 AgentStateStore 持久化层，支持跨进程的会话恢复，并为 4.25 分层内存系统奠定基础。

## What Changes — 变更内容

- **新的 `AgentState` 数据类** — 每会话工作状态的结构化表示：session_id、agent_id、summary、context、permission_context、tool_context、task_context、environment_root、metadata。
- **新的 `AgentStateStore` ABC** — 持久化接口，包含 `save()`、`load()`、`delete()`、`list_sessions()` 方法。
- **新的 `InMemoryStateStore`** — 默认的进程内实现，用于单机使用和测试。
- **`WorkflowExecutionService` 集成** — 在调用入口从存储加载 AgentState，注入到 `execution_context`，在调用退出时保存。
- **`EnvironmentManager` 集成** — environment_root 路径自动填充到 AgentState 中。

## Capabilities — 能力

### 新能力
- `agent-state-separation`：每会话 AgentState 数据模型、AgentStateStore ABC 带 InMemoryStateStore、以及 WorkflowExecutionService 集成的状态加载/保存生命周期。

### 修改的能力
- `agent-environment`：次要变更 — sessions/ 子目录现在用于 AgentState 快照（无需规范级别的需求变更，仅实现细节）。

## Impact — 影响

- **新文件**：`src/hecate/services/state/`（state.py、store.py、__init__.py）
- **修改的文件**：`src/hecate/services/workflow/execution_service.py`（状态加载/保存生命周期）
- **测试**：`tests/test_services/test_state/`（状态模型 + 存储 + 集成）
- **无破坏性变更**：AgentState 是新增功能；现有的 execution_context 行为不变。
- **无新依赖**：使用 Pydantic（已可用）和 asyncio.Lock（标准库）。
