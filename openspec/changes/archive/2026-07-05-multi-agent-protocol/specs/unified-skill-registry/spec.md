## ADDED Requirements — 新增需求

### Requirement: SkillRegistry 解析异构技能引用 — SkillRegistry resolves heterogeneous skill references
系统应提供一个 `SkillRegistry` 服务，将 `SkillRef` 对象（包含 `ref_type` 和 `ref_id`）解析为具有统一元数据的 `ResolvedSkill` 对象，无论底层资源是 Tool、Skill、Knowledge Base、Workflow 还是 Agent。

#### Scenario: 解析工具引用 — Resolve a tool reference
- **WHEN** SkillRegistry.resolve() 接收到 `ref_type: "tool"` 和 `ref_id: "weather_api"` 的 SkillRef
- **THEN** 系统按名称查询 ToolModel，并返回包含 `name`、`description`、`parameters`（JSON Schema）和 `source: "tool"` 的 ResolvedSkill

#### Scenario: 解析知识库引用 — Resolve a knowledge base reference
- **WHEN** SkillRegistry.resolve() 接收到 `ref_type: "knowledge"` 和有效 KB UUID 的 SkillRef
- **THEN** 系统按 UUID 查询 KnowledgeBaseModel，并返回包含 `name`、`description` 和 `source: "knowledge"` 的 ResolvedSkill

#### Scenario: 解析工作流引用 — Resolve a workflow reference
- **WHEN** SkillRegistry.resolve() 接收到 `ref_type: "workflow"` 和有效工作流 UUID 的 SkillRef
- **THEN** 系统按 UUID 查询 WorkflowModel，并返回包含 `name`、`description` 和 `source: "workflow"` 的 ResolvedSkill

#### Scenario: 解析 Agent 引用 — Resolve an agent reference
- **WHEN** SkillRegistry.resolve() 接收到 `ref_type: "agent"` 和有效 Agent UUID 的 SkillRef
- **THEN** 系统按 UUID 查询 AgentModel，并返回包含 `name`、`description` 和 `source: "agent"` 的 ResolvedSkill

#### Scenario: 解析未知引用返回错误 — Resolve unknown reference returns error
- **WHEN** SkillRegistry.resolve() 接收到无法找到的 SkillRef
- **THEN** 系统抛出 `SkillNotFoundError`，包含 ref_type 和 ref_id

### Requirement: SkillRegistry 统一调用已解析的技能 — SkillRegistry invokes resolved skills uniformly
系统应提供一个 `SkillRegistry.invoke()` 方法，通过适当的执行路径（tool_execute、agent_execute、workflow_execute、knowledge_query 或 skill 指令注入）执行任何已解析的技能。

#### Scenario: 调用工具类技能 — Invoke a tool skill
- **WHEN** 使用工具类型的 SkillRef 和参数调用 SkillRegistry.invoke()
- **THEN** 系统委托给 EnginePort.tool_execute()，传入工具名称和参数

#### Scenario: 调用知识类技能 — Invoke a knowledge skill
- **WHEN** 使用知识类型的 SkillRef 和查询调用 SkillRegistry.invoke()
- **THEN** 系统委托给 EnginePort.knowledge_query()，传入 KB ID 和查询

#### Scenario: 调用工作流类技能 — Invoke a workflow skill
- **WHEN** 使用工作流类型的 SkillRef 和输入调用 SkillRegistry.invoke()
- **THEN** 系统委托给 EnginePort.workflow_execute()，传入工作流 ID 和输入

#### Scenario: 调用 Agent 类技能 — Invoke an agent skill
- **WHEN** 使用 Agent 类型的 SkillRef 和任务消息调用 SkillRegistry.invoke()
- **THEN** 系统委托给 EnginePort.agent_execute()，传入 Agent ID 和消息

### Requirement: SkillRegistry 为 LLM 上下文注入格式化技能 — SkillRegistry formats skills for LLM context injection
系统应提供一个 `SkillRegistry.format_for_llm()` 方法，生成已解析技能的统⼀文本表示，适用于 LLM 系统提示注入。

#### Scenario: 为 LLM 格式化工具 — Format tools for LLM
- **WHEN** format_for_llm() 接收包含工具的 ResolvedSkill 列表
- **THEN** 输出包含工具名称、描述和参数模式，采用标准化格式（例如 XML 或 JSON）

#### Scenario: 为 LLM 格式化知识库 — Format knowledge bases for LLM
- **WHEN** format_for_llm() 接收包含知识库的 ResolvedSkill 列表
- **THEN** 输出将每个 KB 描述为可搜索的知识源，并附有检索说明

### Requirement: AgentModel 支持统一的 skill_ids 字段 — AgentModel supports unified skill_ids field
系统应在 AgentModel 上添加一个可选的 `skill_ids` JSON 字段，存储 SkillRef 对象列表，补充（而非替换）现有的 `tools`、`skills`、`knowledge_base_ids` 字段。

#### Scenario: 具有统一 skill_ids 的 Agent — Agent with unified skill_ids
- **WHEN** 使用 `skill_ids: [{"ref_type": "tool", "ref_id": "search"}, {"ref_type": "knowledge", "ref_id": "<uuid>"}]` 创建 Agent
- **THEN** SkillRegistry 应解析两个引用，且该 Agent 应能同时访问该工具和知识库

#### Scenario: 与现有字段的向后兼容性 — Backward compatibility with existing fields
- **WHEN** Agent 的 `tools: ["search"]` 但没有 `skill_ids`
- **THEN** SkillRegistry 仍应通过旧的 `tools` 字段解析该工具，确保现有 Agent 无需迁移即可工作

### Requirement: SkillRegistry 解析 A2A 远程 Agent — SkillRegistry resolves A2A remote agents
系统应支持 SkillRef 中的 `ref_type: "remote_agent"`，解析为通过 AgentCard 发现的 A2A 远程 Agent，使 Agent 能够将远程 A2A Agent 作为技能使用。

#### Scenario: 解析远程 Agent 技能 — Resolve remote agent skill
- **WHEN** SkillRegistry.resolve() 接收到 `ref_type: "remote_agent"` 和 URL 的 SkillRef
- **THEN** 系统获取远程 AgentCard 并返回包含卡片中能力的 ResolvedSkill

#### Scenario: 调用远程 Agent 技能 — Invoke remote agent skill
- **WHEN** 使用 remote_agent 类型的 SkillRef 调用 SkillRegistry.invoke()
- **THEN** 系统委托给 A2AClient.send_message()，传入远程 Agent URL
