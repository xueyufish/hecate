## Context — 背景

Hecate 拥有 `WorkspaceModel` 用于多租户隔离（Organization → Workspace → Agents/Tools/KBs），但缺少统一的 Agent 执行环境抽象。Agent 数据（文件、内存、会话日志）分散在 PostgreSQL、MinIO、Qdrant 和文件系统中，没有生命周期管理。

**研究基础**（14 个平台）：
- Bedrock AgentCore：Agent Runtime + Managed Session Storage（14 天 TTL，每会话 microVM）
- AgentScope：Workspace ABC（Local/Docker/E2B），WorkspaceManager 带 TTL 驱逐，每 Agent 隔离
- Dify：AgentRuntimeSession + AgentDrive（每 Agent 文件系统）
- Claude Code：Working Directory + SessionStore 适配器（S3/Redis/Postgres）
- Google Gemini：SandboxEnvironment（7 天 TTL，env_id 复用）
- Salesforce：Session-scoped（显式创建/删除）
- Palantir AIP：Ontology 作为统一内存（48 小时节点生命周期）
- Huawei AgentArts：Runtime SDK + Memory Bank（独立服务）

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- AgentEnvironment ABC 及 LocalEnvironment（文件系统）实现
- EnvironmentManager 生命周期管理（创建/获取/关闭，带 TTL 驱逐）
- 文件 CRUD API（列出/读取/写入/删除）
- 会话自动关联（通过 agent_id，无需手动 env_id）
- 懒创建（首次文件操作时）
- 可配置 TTL（默认 24 小时，每次交互重置）
- 仅服务层（不涉及引擎层变更，不涉及 EnginePort 变更）

**非目标：**
- DockerEnvironment / E2BEnvironment（1.3.15a，单独特性）
- Context offloading（1.3.15b，单独特性）
- Sandbox environment mount（1.3.15c，单独特性）
- AgentState 分离（1.3.16，单独特性）
- 新 DB 模型（MVP 中无 EnvironmentModel）
- EnginePort 变更（Worker 不直接访问环境）

## Decisions — 决策

### 决策 1：AgentEnvironment，而非 AgentWorkspace

**选择**：将执行环境命名为 "AgentEnvironment"，以避免与 `WorkspaceModel`（多租户隔离边界）冲突。

**理由**：行业研究表明，没有平台对租户隔离和执行环境使用相同术语。Hecate 已使用 "Workspace" 表示租户隔离（`WorkspaceModel`）。使用 "AgentWorkspace" 会造成概念混淆。

### 决策 2：服务层，而非引擎层

**选择**：AgentEnvironment 位于 `services/environment/`，而非 `engine/`。

**理由**：所有平台都在服务层管理执行环境（AgentScope：Agent Service，Bedrock：AgentCore Runtime，Dify：Agent Runtime）。引擎层（agent 循环）消费环境但不管理它。这避免了改动 EnginePort，并保持引擎层零外部依赖的原则不变。

### 决策 3：每 Agent 隔离，而非每会话

**选择**：环境以 `agent_id` 为键。同一 Agent 的所有会话共享一个环境。

**理由**：AgentScope 的内置管理器都按 `agent_id` 隔离。每会话隔离由 1.3.16 Agent State Separation（易失状态）处理。Environment = 持久（每 Agent），AgentState = 易失（每会话）。

### 决策 4：懒创建与 TTL 驱逐

**选择**：环境目录在首次文件操作时创建，而非 Agent 创建时。TTL 默认 24 小时，每次交互重置。

**理由**：所有平台都使用懒创建（Google、Bedrock、AgentScope）。24 小时 TTL 介于 AgentScope（1 小时）和 Bedrock（14 天）之间。TTL 在每次交互时重置（Google 模式），因此活跃的 Agent 永远不会被驱逐。

### 决策 5：无 EnginePort 变更

**选择**：Worker 不直接访问环境。环境信息通过 `execution_context` 字典传递。

**理由**：MVP 范围。Worker 通过 EnginePort 访问工具/知识，但环境主要用于文件管理（API 驱动）和生命周期管理。1.3.15c（Sandbox Environment Mount）将在后续添加 EnginePort 集成。

## Risks / Trade-offs — 风险 / 权衡

- **[仅单实例]** — LocalEnvironment 使用文件系统，不适合多实例部署。缓解：通过 AgentEnvironment ABC 抽象；后续可添加 MinIO/S3 后端。
- **[无 DB 模型]** — 没有环境的元数据（大小、创建时间）。缓解：如果需要管理 UI，后续可以添加 EnvironmentModel。
- **[TTL 驱逐竞态]** — 两个并发请求可能在驱逐上产生竞态。缓解：EnvironmentManager 使用 asyncio.Lock 实现线程安全。
