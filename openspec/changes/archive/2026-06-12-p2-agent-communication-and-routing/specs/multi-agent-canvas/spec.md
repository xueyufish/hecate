## ADDED Requirements — 新增需求

### Requirement: Channel access summary in config panel — 配置面板中的通道访问摘要
Agent 节点配置面板应显示一个通道访问摘要部分，展示所选 agent 可以从哪些通道读取和写入。摘要应按类型（LAST_VALUE、TOPIC、ACCUMULATOR）对通道进行分组，并高亮广播参与（与其他 agent 共享的 TOPIC 通道）。

#### Scenario: Channel access summary displayed — 显示通道访问摘要
- **当** 用户选择一个已声明 `channels.readable: ["messages", "context"]` 和 `channels.writable: ["messages"]` 的 agent 节点
- **则** 配置面板显示一个"通道访问"部分，包含"可读：messages (topic), context (last_value)"和"可写：messages (topic)"

#### Scenario: Broadcast participation highlighted — 高亮广播参与
- **当** 用户选择一个与其他 2 个 agent 节点共享 TOPIC 通道 "shared_context" 的 agent 节点
- **则** 通道访问摘要应使用广播图标高亮 "shared_context"，并显示"与 2 个 agent 共享"

#### Scenario: No channel declaration shown as informational — 无通道声明显示为信息提示
- **当** 用户选择一个没有 `channels` 配置的 agent 节点
- **则** 配置面板显示"未配置通道访问"，并附带配置通道访问的建议

### Requirement: Routing mode configuration for condition nodes — 条件节点的路由模式配置
Condition 节点配置面板应提供一个路由模式选择器，包含 3 个选项："Condition"（基于表达式）、"Intent"（模式 + LLM 分类）和"Dynamic"（LLM 选择下一个发言者）。每个模式应显示模式特定的配置字段。

#### Scenario: Condition mode selected (default) — 选择 Condition 模式（默认）
- **当** 用户选择一个 condition 节点且路由模式为 "condition"（或未设置）
- **则** 配置面板显示现有表达式字段，无额外路由字段

#### Scenario: Intent mode selected — 选择 Intent 模式
- **当** 用户将路由模式更改为 "Intent"
- **则** 配置面板显示"意图模式"部分，包含添加/删除模式行（每行包含模式正则和目标节点选择器），以及一个可选的用于 LLM 后备的"路由提示"文本字段

#### Scenario: Dynamic mode selected — 选择 Dynamic 模式
- **当** 用户将路由模式更改为 "Dynamic"
- **则** 配置面板显示一个列出图谱中所有 agent 节点的"候选 Agent"多选、一个"路由提示"文本字段和一个"允许重复发言者"开关（默认关闭）

#### Scenario: Routing mode persisted to graph DSL — 路由模式持久化到 Graph DSL
- **当** 用户选择 "Intent" 模式并添加意图模式
- **则** Graph DSL 节点配置应包含 `routing_mode: "intent"` 和 `routing_config: {intent_patterns: [...], routing_prompt: "..."}`

### Requirement: Dynamic handoff edge type in canvas — 画布中的动态 Handoff 边类型
边类型选择器应将"动态 Handoff"作为第 5 个选项包含在内。动态 handoff 边应渲染为带闪光图标的紫色虚线，与静态 handoff 边（紫色虚线，无闪光图标）在视觉上有所区分。

#### Scenario: User creates dynamic handoff connection — 用户创建动态 Handoff 连接
- **当** 用户连接 agent 节点 A 到 agent 节点 B 和 C，并在边类型选择器中选择"动态 Handoff"
- **则** Graph DSL 将边存储为 `trigger: "dynamic_handoff"` 和 `target: {"b": "agent_b", "c": "agent_c"}`

#### Scenario: Dynamic handoff edge rendered distinctly — 动态 Handoff 边渲染独特样式
- **当** 图谱包含一条 `trigger: "dynamic_handoff"` 的边
- **则** 画布将该边渲染为带闪光图标的紫色虚线 Bezier 曲线，与静态 handoff 边不同
