## Context — 背景

Hecate 的 P2 多 Agent 编排已完成 95%（60/63）。引擎已支持：
- 6 种协作模式（SEQUENTIAL、PARALLEL、HANDOFF、BROADCAST、NEGOTIATION、DEBATE）
- 基于表达式的路由和多键条件边的 CONDITION 节点
- 带循环检测和自动注入 `handoff_to_agent` 工具的 Handoff 边
- 通道类型（LAST_VALUE、TOPIC、ACCUMULATOR）及 ChannelBehavior ABC
- Graph DSL 模式中的每节点 `channels: { readable: [...], writable: [...] }` 配置
- 带边类型区分（default、handoff、conditional、fan-out）的画布 UI
- 用于每通道读/写切换的 ChannelSelector 组件

当前缺失的是：通道访问虽已声明但从未强制执行，路由仅限于静态表达式求值。企业平台提供了基于意图和动态 LLM 驱动的路由。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 在编译时强制执行通道访问边界（软验证：警告，不阻止）
- 当节点访问其声明范围之外的通道时添加运行时警告
- 增强 ChannelSelector UX，添加广播模式可视化和访问摘要
- 扩展 CONDITION 节点，支持 3 种路由模式：condition（现有）、intent（新增）、dynamic（新增）
- 支持 LLM 在运行时选择目标的动态 handoff 边
- 保持向后兼容 — 没有 routing_mode 的现有图谱继续正常工作

**非目标：**
- 硬通道隔离（在未授权访问时抛出错误）— 对现有图谱破坏性太大
- 用于路由的新节点类型 — 扩展 condition 节点即可
- LLM 调用缓存或批处理用于动态路由 — 后续优化
- 自定义路由函数支持（如 AutoGen 的 selector_func）— 不在 P2 范围内
- 协作模式推断逻辑的变更 — 模式与路由模式正交

## Decisions — 决策

### D1：将 2.7b + 2.7c 合并为单一变更

**决策**：将两个功能一起实现。
**理由**：同一领域（多 agent 协调），共享画布配置面板基础设施，合并的 S+M 工作量对于一个变更来说是合理的。
**备选方案**：分开的变更会重复画布配置面板工作，并存在 DSL 模式变更不一致的风险。

### D2：通道访问方法 — 软验证（选项 B）

**决策**：编译时检查记录通道访问违规的警告。运行时在访问未声明通道时记录警告。两者均不阻止执行。
**理由**：硬隔离（抛出错误）会破坏未声明通道访问的现有图谱。纯声明式（无运行时检查）不提供任何价值。软验证在保持一切正常工作的同时为用户提供可见性。
**备选方案 A（硬隔离）**：违规时抛出 `GraphValidationError`。破坏性太大。
**备选方案 C（仅声明式）**：仅验证模式，无运行时执行。无实际保护。

### D3：路由模式扩展现有 CONDITION 节点

**决策**：向 CONDITION 节点配置添加 `routing_mode` 字段（默认值："condition"）和 `routing_config` 字段。无需新节点类型。
**理由**：路由是条件分支的一种变体 — 它属于 condition 节点。添加新节点类型需要全局的模式/编译器/运行时/画布变更。字段扩展是最小且向后兼容的。
**备选方案**：新的 ROUTER 节点类型。因实现成本及与 CONDITION 的概念重叠而被拒绝。

### D4：动态路由使用 EnginePort.llm_invoke()

**决策**：当 `routing_mode="dynamic"` 时，条件求值路径使用路由提示调用 `EnginePort.llm_invoke()` 以从候选 agent 中分类下一个发言者。
**理由**：EnginePort 已提供 `llm_invoke()` 作为标准 LLM 调用接口。动态路由本质上是 LLM 分类任务 — 完美契合。
**备选方案**：专用路由服务。对于 P2 过于工程化；EnginePort 是合适的抽象级别。

### D5：动态 handoff 使用现有 handoff 基础设施

**决策**：添加 `"dynamic_handoff"` 边触发器。当存在时，handoff 工具仍然自动注入，但目标列表包括从源节点可到达的所有 agent 节点。LLM 在运行时决定调用哪个目标。
**理由**：重用现有的 handoff 循环检测和工具注入。唯一的变化是目标参数不限于单个 agent。
**备选方案**：单独的"transfer"工具（Google ADK 模式）。已拒绝 — handoff 已实现此功能，只需多目标支持。

### D6：意图路由优先使用模式匹配，LLM 作为后备

**决策**：`routing_mode="intent"` 首先评估 `intent_patterns`（正则表达式 → 目标）。如果没有模式匹配，则回退到使用 `routing_prompt` 的 LLM 分类。
**理由**：模式匹配快速、免费且确定性强。LLM 后备处理边缘情况。两层方法让用户控制常见路径，同时保持灵活性。

## Risks / Trade-offs — 风险 / 权衡

**[R1] 动态路由延迟** → 每个动态路由决策都需要一次 LLM 调用。缓解措施：记录动态路由会增加延迟。用户可以使用意图模式（模式匹配）进行快速路径，为需要 LLM 判断的情况保留动态路由。

**[R2] LLM 路由不稳定性** → LLM 可能返回无效的 agent 名称或不一致的路由决策。缓解措施：根据 `candidate_agents` 列表验证 LLM 响应。如果响应无效，回退到 "default" 目标。

**[R3] 通道访问验证误报** → 编译时检查可能对合法的动态通道访问模式发出警告。缓解措施：警告不阻塞；用户可以忽略它们。记录动态模式可能触发误报。

**[R4] 现有图谱的模式迁移** → 向 condition 节点配置添加 `routing_mode` 和 `routing_config` 会更改 DSL 模式。缓解措施：两个字段都是可选的，具有合理的默认值。现有图谱无需更改即可编译。

**[R5] 条件求值中的 EnginePort 依赖** → 动态/意图路由在之前纯计算的条件求值路径中引入了 LLM 调用。缓解措施：仅在 `routing_mode` 为 "intent" 或 "dynamic" 时激活。默认 "condition" 模式零开销。

## Open Questions — 开放问题

无 — 所有关键决策均在探索阶段预先批准。
