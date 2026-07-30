## Context — 背景

自 2026-05-06 统一执行引擎变更以来，Hecate 的 Pregel 运行时已完全功能化。运行时已经支持每个 superstep 多个节点（通过 `PregelRuntime.superstep()` 中的 `current_nodes` 列表），但图 DSL 和编译器没有表达并行语义的方式。

当前图 DSL Graph Schema：
- 6 种节点类型：CONVERSATION、TOOL_CALL、CONDITION、AGENT、KNOWLEDGE_RETRIEVAL、VARIABLE_SET
- 1 种边类型：带有 `source`/`target` 引用的 `Edge`
- 1 个图结构：平面的 `nodes`/`edges` 列表

此次变更将 DSL 扩展到 10 种节点类型并增加类型化边。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- **Fan-out/Fan-in**：向 DSL 添加 `FAN_OUT` 和 `MERGE` 节点类型。Fan-out 节点引用一个目标节点集（分支）。Merge 节点等待所有分支完成，然后使用可配置的 reducer 聚合。
- **子图**：向 DSL 添加 `SUBGRAPH` 节点类型。子图拥有自己的 `nodes`/`edges` 列表和输入/输出通道映射。编译器递归解析子图，展平为单个执行计划。
- **多代理模式**：添加 6 个模板工厂，用于层级、交接、管道、广播、协商和辩论模式——每个都使用标准节点和边生成一个有效的 DSL 图。
- **类型化边**：添加 `EdgeType` 枚举（MESSAGE、CONDITIONAL、CONTROL、SUBSCRIBE）。编译器验证边类型兼容性（例如，CONDITIONAL 边必须连接到 CONDITION 节点）。
- **向后兼容性**：没有边类型的现有图默认使用 `EdgeType.MESSAGE`。

**非目标：**

- 运行时动态 fan-out（所有分支在编译时已知）
- 跨子图的持久状态共享（每个子图是隔离的）
- DSLA 用于代理路由的高级声明性语言——模式模板是工厂函数，不是新的 DSL
- 协商/辩论收敛算法的自定义——使用固定的简单多数制/100 轮限制

## Decisions — 设计决策

### D1：Fan-out 是显式的 DSL 节点

**选择**：`FAN_OUT` 是一个 DSL 节点，带有 `branches: list[str]` 属性。编译器在准备就绪时将消息复制到所有分支。

**理由**：这与当前编译器如何处理节点一致。Fan-out 节点是图的显式部分，使其对用户可见且可调试。

**备选方案**：隐式 fan-out（基于出度检测）。否决：对用户隐藏了并发性。

### D2：Merge 使用可配置的 reducer

**选择**：`MERGE` 节点有 `reducer: str` 属性，值为 `"concat"`（默认——连接结果）、`"select_first"`（使用第一个非空结果）或 `"custom"`（调用用户函数）。`"custom"` reducer 引用一个注册的自定义 reducer 函数名。

**理由**：不同的场景需要不同的合并语义。连接用于并行检索（RAG），select-first 用于带有回退的编排，自定义用于复杂聚合。

### D3：子图被展平

**选择**：编译器递归解析 `SUBGRAPH` 节点，在其父图中包含/排除它们，将它们展平为单个执行计划。子图输出通道被重命名为避免冲突。

**理由**：Pregel 运行时操作于平面节点集。展平避免了修改运行时。

**备选方案**：在运行时保留子图作为嵌套执行上下文。否决：为运行时增加了不必要的复杂性。

### D4：模式模板是工厂函数

**选择**：模式在 `engine/patterns/` 中定义为接受参数（agent_id、model_name）并返回有效 DSL 字典的工厂函数。

**理由**：模式是*约定*，不是 DSL 特性。工厂函数使它们明确且可组合。

**备选方案**：DSL 中的声明性模式关键字。否决：在 P3 之前为 DSL Schema 增加了不必要的复杂性。

### D5：类型化边默认向后兼容

**选择**：`EdgeType` 枚举（MESSAGE、CONDITIONAL、CONTROL、SUBSCRIBE）。未指定边类型的现有图默认使用 `EdgeType.MESSAGE`。

**理由**：零破坏性变更。现有的 2026-05-06 和 2026-05-26 图无需修改即可继续工作。

**备选方案**：每次都要求边类型。否决：破坏了现有的图定义。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 扇出分支同时完成增加运行时复杂性 | 运行时在进入下一个 superstep 之前等待所有分支 |
| 子图展平丢失上下文信息 | 编译器在展平时为子图节点添加 `subgraph_id` 属性 |
| 协商模式的收敛限制可能不符合预期 | 100 轮限制可配置；有 defaults.py 常量 |
| 类型化边可能被忽略 | 编译器验证边类型兼容性；不兼容时引发 SchemaError |