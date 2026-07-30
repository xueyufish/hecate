## Why — 为什么

A2A 协议（Google 的 Agent-to-Agent）已成为 Agent 间通信的事实行业标准，150+ 组织在生产中使用（2026 年 7 月），8 个 Linux Foundation 白金成员（AWS、Cisco、Google、IBM、Microsoft、Salesforce、SAP、ServiceNow）在管理 TSC 中。所有竞争协议（IBM ACP、AGNTCY ACP）已汇聚到 A2A。Hecate 当前的多 Agent 能力（6 种协作模式、EventBus、TaskAllocator、P2P Negotiator）都是进程内完成的——没有用于跨平台 Agent 发现和委托的协议层。

同时，Hecate 的技能/工具/知识/工作流关联是分散的：工具和技能使用名称字符串，知识库使用 UUID，工作流使用单个 UUID 引用。行业中没有一个平台将这些统一为单一的"Skill"抽象——这是 Hecate 的差异化机会。Agent-工作流相互嵌入目前是单向的（AGENT 节点存在于 DAG 中，但 Agent 不能将工作流作为技能调用）。

## What Changes — 变更内容

- **A2A 协议 (2.10)**：新的 `a2a/` 模块，实现 A2A v1.2——Hecate 同时作为 A2A 服务器（AgentCard、JSON-RPC 任务生命周期、SSE 流式、artifacts）和 A2A 客户端（Agent 发现、任务提交、推送通知接收器）。使用官方 `a2a-sdk` Python 包。
- **签名 Agent Card (2.10a)**：使用 ES256 算法的 JWS 签名、RFC 8785 JSON 规范化、在 `/.well-known/jwks.json` 的 JWKS 公钥分发、算法固定以防止降级攻击。
- **统一技能注册表 (2.9)**：`SkillRegistry` 服务，将 Tools、Skills、Knowledge Bases、Workflows 和 Agents 统一为单一的 `SkillRef` 抽象，包含 `resolve()`、`invoke()`、`format_for_llm()`。零数据迁移——从现有表读取。
- **Agent-工作流相互嵌入 (2.9a)**：Agent → 工作流作为工具调用（扩展 EnginePort，添加 `workflow_execute()`）；工作流 → Agent（AGENT 节点类型已存在）。递归嵌套，`max_depth=3`（IBM 反模式指南）。
- **协作冲突处理 (2.8)**：扩展现有的 `ConflictResolver`，添加分布式锁协调、任务级冲突检测、A2A Agent 的权限范围不匹配处理、与 `P2PNegotiator` 集成。

## Capabilities — 能力

### 新能力
- `a2a-protocol`：A2A v1.2 服务器（AgentCard、JSON-RPC、SSE、任务生命周期、artifacts、推送通知）和客户端（发现、任务提交）实现
- `signed-agent-cards`：Agent Card 的 JWS 签名生成和验证，带 JWKS 密钥分发
- `unified-skill-registry`：SkillRegistry 服务，将 Tools、Skills、Knowledge Bases、Workflows 和 Agents 抽象为统一的 SkillRef 条目
- `agent-workflow-embedding`：双向 Agent ↔ 工作流调用，带递归嵌套（max_depth=3）

### 修改的能力
- `event-bus`：添加 A2A 特定的 CollaborationEventType 条目（A2A_TASK_DELEGATED、A2A_ARTIFACT_RECEIVED），用于跨协议事件关联
- `agent-tool`：扩展 AgentTool，支持 A2A 远程 Agent 作为调用目标（不仅是本地的 agent_execute）

## Impact — 影响

- **新代码**：`src/hecate/a2a/` 模块（server、client、signing）、`src/hecate/skill_registry/` 模块、工作流嵌入服务
- **修改的代码**：`engine/eventbus.py`（新事件类型）、`engine/agent_tool.py`（A2A 目标支持）、`engine/temporal/conflict.py`（分布式冲突）、`services/orchestration/agent_execution_port.py`（workflow_execute）、`models/agent.py`（统一的 skill_ids 字段）、`api/`（A2A 端点 + 技能注册表 API）
- **新依赖**：`a2a-sdk`（官方 Python SDK）、`cryptography`（已通过 auth 模块存在）
- **迁移**：添加 `agent_card_keys` 表用于签名密钥对，`a2a_tasks` 表用于任务生命周期持久化
- **配置**：新的 `A2A_*` 设置（服务器 URL、签名密钥路径、JWKS URL）、`SKILL_REGISTRY_*` 设置
