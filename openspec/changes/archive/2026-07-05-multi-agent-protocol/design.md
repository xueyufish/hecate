## Context — 背景

Hecate 拥有成熟的多 Agent 栈：6 种协作模式（Sequential、Parallel、Handoff、Broadcast、Negotiation、Debate）、EventBus 发布/订阅、TaskAllocator、P2P Negotiator、通过 EnginePort 实现的 Agent-as-Tool。所有通信都是进程内完成的——没有用于跨平台 Agent 发现和委托的协议层。

A2A 协议（v1.2，2026 年 3 月）是行业标准：150+ 组织在生产中使用，5 个官方 SDK，Linux Foundation 治理。Hecate 的 MCP 客户端/服务器双向架构证明了协议层集成是有效的。A2A 与 MCP 互补：A2A 处理 Agent 到 Agent（水平），MCP 处理 Agent 到工具（垂直）。

技能/工具/知识/工作流的关联分散在 4 种不同模式中（names、names、UUIDs、single-UUID）。没有行业平台将这些统一为单一的"Skill"抽象——Hecate 可以在这里实现差异化。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- A2A v1.2 协议合规：Hecate 同时作为 A2A 服务器和客户端
- 使用 JWS(ES256) + RFC 8785 + JWKS 的签名 Agent Card
- 统一的 SkillRegistry，抽象 5 种资源类型（Tool、Skill、KB、Workflow、Agent）为 SkillRef
- 双向 Agent ↔ Workflow 嵌入，最大深度 max_depth=3
- 为分布式/A2A 场景扩展冲突处理
- 技能统一无需数据迁移（SkillRegistry 读取现有表）

**非目标：**
- gRPC 传输绑定（推迟到后续；MVP 为 JSON-RPC over HTTP + SSE）
- A2A REST 端点等价物（MVP 为 JSON-RPC；REST 是后续）
- AGNTCY Directory 集成（单独的未来变更）
- AP2（Agent Payments Protocol）扩展（P5 范围）
- OAuth2 流程支持（MVP 仅 APIKey + HTTP Bearer；OAuth2/OIDC/mTLS 推迟）
- `ListTasks` 分页（推迟；基本任务生命周期为 MVP）
- 自定义 A2A 扩展（推迟；MVP 仅标准规范）
- 技能注册表或 A2A 的 UI/Canvas 变更（本次变更为仅后端）

## Decisions — 决策

### Decision 1: 使用官方 `a2a-sdk` Python 包

**选择**：使用 `a2a-sdk`（来自 a2aproject/a2a-python 的官方 Python SDK）

**备选方案**：
- *从头实现*（如引擎层理念）：工作量更大（~2000 LOC），必须手动跟踪规范变更，有协议不合规风险。
- *引入 SDK 源码*：Fork 维护负担。

**理由**：MCP 客户端已使用官方 `mcp` SDK——在服务层使用协议 SDK 有先例。`a2a-sdk` 已可用于生产，具有 FastAPI 集成（`A2AFastAPIApplication`）、任务生命周期管理、SSE 流式和签名支持。使 Hecate 保持在规范兼容的路径上，无需重新发明协议机制。SDK 位于服务层，而非引擎层——引擎保持零外部依赖。

### Decision 2: SkillRegistry 作为服务层，而非新模型

**选择**：`SkillRegistry` 从现有的 SkillModel、ToolModel、KnowledgeBaseModel、WorkflowModel、AgentModel 读取——没有新的 UnifiedSkillModel 表。

**备选方案**：
- *新的 UnifiedSkillModel 表*：单一真实源但需要数据迁移，创建同步/重复问题，破坏现有的 CRUD API。
- *Python Protocol/Union 类型*：类型安全但运行时解析仍然需要；不解决持久化问题。

**理由**：零迁移风险。现有的 CRUD API 保持不变地工作。SkillRegistry 是只读侧抽象，在服务层统一。权衡是注册表中的跨表解析逻辑，这是可接受的。

### Decision 3: A2A 模块结构镜像 MCP

**选择**：`src/hecate/a2a/` 模块，包含 `server/`、`client/`、`signing.py`、`types.py` 子模块。

**理由**：镜像已验证的 `src/hecate/services/mcp/` 结构。服务器（Hecate 作为远程 Agent）和客户端（Hecate 调用远程 Agent）之间清晰分离。签名为两者共享。

### Decision 4: 通过 EnginePort 扩展实现工作流嵌入

**选择**：扩展 `EnginePort`，添加可选的 `workflow_execute()` 方法（默认为 NotImplementedError，与 `agent_execute()` 相同）。重用现有的 PregelRuntime 进行执行。

**备选方案**：
- *新的 WorkflowSkill 包装类*：更多间接性，重复 AgentTool 模式。
- *子图组合（LangGraph 风格）*：复杂的状态共享，难以调试。

**理由**：与现有的 Agent-as-Tool 模式一致。AgentTool 包装 AgentDefinition；类似地，WorkflowTool 包装 workflow_id。两者都委托给 EnginePort 方法。通过上下文栈强制执行 max_depth=3。

### Decision 5: 冲突处理扩展现有的 ConflictResolver

**选择**：向现有的 `ConflictResolver` 添加分布式冲突模式（resource、task、state、permission），而非创建新类。

**理由**：ConflictResolver 已有 4 种策略（LWW、MERGE_LIST、MERGE_MAP、HUMAN_APPROVAL）。扩展分布式模式（分布式锁、协商、升级）是添加式的且向后兼容。

### Decision 6: 签名密钥存储在数据库中，支持轮换

**选择**：`AgentCardKeyModel` 表存储密钥对（kid、private_key、public_key、algorithm、created_at、rotated_at）。通过新密钥生成 + 旧密钥宽限期实现轮换。

**备选方案**：
- *基于文件的密钥*：在多副本部署中更难轮换。
- *Vault 集成*：MVP 来说过度工程；以后可与现有的 SecretProviderABC 集成。

**理由**：基于数据库的密钥适用于多副本部署（所有副本从同一数据库读取）。宽限期允许轮换期间进行中的验证完成。

## Risks / Trade-offs — 风险 / 权衡

- **[a2a-sdk 版本变动]** → 在 pyproject.toml 中固定到特定版本；每季度跟踪规范更新。
- **[SkillRegistry 跨表查询]** → 将技能索引反规范化到轻量级视图或缓存中；接受最终一致性。
- **[工作流嵌套深度 >3 降低推理质量]** → 在 EnginePort 级别强制执行 max_depth=3，带清晰的错误消息；在 depth=2 时记录警告。
- **[A2A 任务生命周期持久化]** → 使用现有的异步 SQLAlchemy + PostgreSQL；仅测试时使用 InMemoryTaskStore。
- **[签名卡密钥泄露]** → 密钥轮换 API + 宽限期；在签名验证失败率激增时发出告警。
- **[实现期间协议规范漂移]** → 目标 v1.2（自 2026 年 3 月稳定）；避免前沿功能。

## Migration Plan — 迁移计划

1. **阶段 1 — Skill Registry（向后兼容）**：添加 SkillRegistry 服务，读取现有表。添加 `agent.skill_ids` JSON 字段（SkillRef 列表），与现有的 `tools`/`skills`/`knowledge_base_ids` 字段并存。旧字段保持权威；新字段为可选加入。
2. **阶段 2 — 工作流嵌入**：扩展 EnginePort，添加 `workflow_execute()`。添加 WorkflowTool 包装器。无需迁移——纯新增。
3. **阶段 3 — A2A Server**：在 `/.well-known/agent-card.json` 和 `/a2a/`（JSON-RPC）处的新端点。对现有 API 无影响。迁移添加 `a2a_tasks` 表。
4. **阶段 4 — A2A Client**：新的 A2AClient 类。无需迁移。添加 `remote_agents` 配置，用于受信任的外部 A2A 端点。
5. **阶段 5 — 签名卡**：新的 `agent_card_keys` 表。签名是按工作区可选加入的。未签名卡可以工作但记录 WARNING。
6. **阶段 6 — 冲突处理**：扩展 ConflictResolver，添加分布式模式。现有的 4 种策略不变。

**回滚**：每个阶段可独立部署。A2A 服务器可通过配置标志禁用（`A2A_SERVER_ENABLED=false`）。SkillRegistry 是只读的——禁用它可回退到现有的基于字段的关联。

## Open Questions — 开放问题

- **A2A 任务持久化**：使用现有的 PostgreSQL 还是单独的 A2A 特定数据库？→ 默认：同一数据库，单独的 `a2a_tasks` 表。
- **SkillRegistry 缓存**：内存缓存带 TTL 还是 Redis？→ 默认：MVP 使用内存，生产使用 Redis（与现有模式一致）。
- **AgentCard 生成**：静态配置还是每个 Agent 动态生成？→ 默认：混合——基础卡片来自配置，技能数组来自 Agent 关联的技能。
