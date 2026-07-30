## Context — 背景

Feature 5.9 是最后一个 P1 缺口。当前状态：

- `SkillModel` ORM 已存在，包含完整的字段集（name、description、source、instructions、allowed_tools、metadata、scripts、references、max_tokens、auto_load），但**没有 `workspace_id`**——唯一缺少多租户隔离的资源模型。
- `AgentModel.skills` 是一个 JSON 列表字段，始终为空，没有任何代码读取它。
- 存在只读 API：`GET /api/skills`、`GET /api/skills/{id}`。
- 不存在技能加载、注入或激活机制。
- 执行流水线有明确的系统提示词注入点：`WorkflowExecutionService.execute(system_prompt=...)` 和 `AgentExecutionPort.agent_execute()`（后者已使用 `agent.persona`）。

功能目录引用了 SKILL.md 格式、多源发现、Plan Agent 自动选择和 Skill Play——这些都是 P2+ 特性。P1 范围仅限于基于数据库的技能加载和系统提示词注入。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 完成最后一个 P1 缺口：agent 可以通过可重用的技能指令进行扩展
- 通过 `workspace_id` 实现多租户技能隔离（与 AgentModel、ToolModel 等一致）
- 使用行业标准的 XML 格式将技能指令注入 agent 系统提示词
- 用于技能管理的完整 CRUD API
- SKILL.md 导入端点，用于解析 YAML 前置元数据 + Markdown 正文
- 通过 API 管理 agent-技能关联

**非目标：**
- 基于文件系统的技能发现（扫描目录中的 SKILL.md 文件）— P2
- 远程技能注册中心/市场 — P2
- Plan Agent 基于任务分析自动选择技能 — P2
- Skill Play 咨询模式 — P2
- 渐进式公开 Level 3（按需资源加载）— P2
- 技能生命周期事件推送（SkillLoadedEvent 等）— P2

## Decisions — 设计决策

### D1: SkillModel 添加 workspace_id

**决策**：向 `SkillModel` 添加 `workspace_id` 列，匹配 AgentModel/ToolModel/KnowledgeBaseModel 的模式。系统技能使用零 UUID `00000000-0000-0000-0000-000000000000`。唯一索引从 `(name)` 更改为 `(workspace_id, name)`。

**理由**：所有其他资源模型都有 `workspace_id`。没有它，技能将在租户间全局共享——这是数据隔离违规。`source` 字段（`system`/`user`/`project`）自然映射到工作空间范围。

**考虑的替代方案**：保持技能全局，使用 agent-技能关联进行隔离。被否决因为：(1) 列表 API 会泄露跨租户的技能名称，(2) 与所有其他模型不一致，(3) P3 多租户迁移将具有破坏性。

### D2: Agent.skills 存储技能名称字符串

**决策**：`AgentModel.skills` 存储技能名称字符串列表（例如 `["code-review", "unit-test"]`），与 `SkillModel.name` 匹配。

**理由**：技能名称在工作空间内是唯一的（通过索引）。名称是人类可读、可调试的，并且与 SKILL.md 文件名约定匹配。UUID 需要额外的联接，没有任何好处。

**考虑的替代方案**：存储技能 UUID。被否决因为：(1) 在 API 响应和数据库查询中可读性较差，(2) 技能名称在每个工作空间中已经是唯一的，(3) CrewAI 和 Claude Code 都使用基于名称的引用。

### D3: 单个系统消息中的 XML 标签格式

**决策**：在单个系统消息中以 XML 格式将技能指令注入系统提示词：

```
{persona}

<skills>
<skill name="code-review">
{description}

{instructions}
</skill>
</skills>
```

**理由**：行业共识——CrewAI、Claude Code、DeerFlow（字节跳动）、GPTMe、IronClaw、OpenDerisk 和 DeepResearchAgent 都使用 `<skill name="...">...</skill>` XML 标签。单个系统消息避免了 engine 层的更改（Hecate 的 `system_prompt` 是一个传递给 `build_chat_graph()` 的字符串）。

**考虑的替代方案**：每个技能一个 SystemMessage（DeerFlow 模式）。被否决因为需要更改 engine 层的消息处理。

### D4: SkillLoader 作为独立服务

**决策**：在 `services/skill/loader.py` 中创建 `SkillLoader` 类，负责：
1. 按 agent ID 加载技能（查询 agent.skills → 按名称 + 工作空间查询 SkillModel）
2. 将指令格式化为 XML 上下文块
3. 遵循 `auto_load` 标志和 `max_tokens` 预算
4. 返回格式化字符串以用于系统提示词注入

**理由**：关注点分离——加载逻辑独立于注入点。`WorkflowExecutionService` 和 `AgentExecutionPort` 都可以使用同一个加载器。可独立测试。

**考虑的替代方案**：在 `execute()` 内联加载。被否决因为：两个注入点之间的逻辑重复，更难测试。

### D5: 系统提示词构建

**决策**：`WorkflowExecutionService.execute()` 将：
1. 如果提供了 `agent_id`，按 ID 加载 agent
2. 调用 `SkillLoader.format_skills(agent_id, workspace_id)` 获取 XML 块
3. 构造 `system_prompt = persona + "\n\n" + skills_block`
4. 传递给 `build_chat_graph(system_prompt=...)`

对于 `AgentExecutionPort.agent_execute()`：
1. 已经按 ID 加载 agent
2. 调用相同的 `SkillLoader.format_skills()`
3. 将 `system_message = {"role": "system", "content": persona}` 替换为 persona + skills

### D6: SKILL.md 导入格式

**决策**：导入端点接受带 SKILL.md 文件内容的多部分表单。解析器提取：
- YAML 前置元数据（在 `---` 分隔符之间）→ name、description、metadata
- Markdown 正文（前置元数据之后）→ instructions

映射到现有的 SkillModel 字段。如果前置元数据缺少必填字段，则使用默认值或引发验证错误。

## Risks / Trade-offs — 风险与权衡

**[风险] 系统提示词 token 溢出** → 包含大型指令的多个技能可能超出模型上下文窗口。缓解措施：SkillModel 上的 `max_tokens` 字段用于每个技能预算；SkillLoader 将总技能块截断以符合预算。P1 使用简单截断，P2 可以添加更智能的压缩。

**[风险] 对现有技能表的破坏性变更** → 添加 `workspace_id` 需要迁移。缓解措施：迁移添加具有默认零 UUID 的列（匹配当前行为），更新索引。现有数据不受影响。

**[风险] 添加 workspace_id 后的名称冲突** → 现有 `idx_skills_name` 是全局唯一的。添加 `workspace_id` 后，相同名称可以存在于不同的工作空间中。迁移删除旧索引，创建新的复合索引。零停机，因为现有行都获得零 UUID。

**[权衡] P1 范围排除文件系统发现** → 用户必须通过 API 或导入端点创建技能，而不是将 SKILL.md 文件放入目录。这对于 P1 是可以接受的（企业用户通过 API 管理）。P2 添加文件系统监视。
