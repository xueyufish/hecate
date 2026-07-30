## Why — 动机

当前 Graph DSL 支持 6 种节点类型（CONVERSATION、TOOL_CALL、CONDITION、AGENT、KNOWLEDGE_RETRIEVAL、VARIABLE_SET），但缺少确定性多步骤管道的两种基本模式：

1. **并行执行（Fan-out/Fan-in）** —— 无法表达"同时运行 A、B、C 并合并它们的结果"。Pregel 运行时已经支持每个 superstep 执行多个节点（通过 `current_nodes` 列表），但没有节点类型或边语义将并行分发和结果聚合形式化。

2. **子图组合** —— 复杂管道必须作为单一平面节点列表定义，没有嵌套或可重用性。无法定义一个节点集作为一个可组合单元进行管理的"子管道"。

此次变更通过 4 个互补的特性填补了这些空白：扇出/合并节点、子图、多代理编排模式和类型化边。

## What Changes — 变更内容

- 向 DSL 添加 **Fan-out 和 Merge** 节点类型：Fan-out 节点将消息分发到多个并行分支；Merge 节点使用可配置的 reducer（concat、select_first、custom）聚合结果。
- 向 DSL 添加 **Subgraph** 节点类型：允许将节点组封装为一个可重用的子图，具有自己的输入/输出通道映射。
- 通过模板添加 **多代理编排模式**：层级（manager → worker）、交接（agent 传递）、管道（顺序阶段）、广播（扇出到所有）、协商（agent 辩论——限制为 100 轮收敛）、辩论（意见交换）。
- 向边定义添加 **类型化边**：消息边（数据流）、条件边（分支）、控制边（执行顺序）、订阅边（事件驱动触发）。
- 更新 Graph DSL JSON Schema 以反映所有 4 个变更。

## Capabilities — 能力变更

### 新增能力
- `fan-out-merge`: Fan-out/Merge 节点，用于 DSL 中的并行执行
- `subgraph`: DSL 中可组合、可重用的子图
- `multi-agent-patterns`: 通过图模板编排 6 种多代理模式
- `typed-edges`: 4 种边类型（消息、条件、控制、订阅）

### 修改的能力
- `graph-dsl`: 扩展 DSL Schema 以包含新的节点类型和边属性

## Impact — 影响范围

- **新文件**: `src/hecate/engine/graph_dsl.py` 中的 fan_out、merge、subgraph 节点处理代码；模式模板（单独的 `patterns/` 模块或图 DSL 中的工厂方法）；类型化边枚举
- **修改的文件**: `src/hecate/engine/graph_dsl.py`（编译器——detect_fan_out、detect_merge、resolve_subgraphs、边类型解析），`src/hecate/engine/pregel.py`（运行时——Fan-out/Merge 的 superstep 调度增强），Graph DSL JSON Schema（所有 4 个新特性）
- **新测试**: 扇出/合并、子图编译、模式模板生成、边类型验证的测试
- **无破坏性变更**: 所有现有图保持向后兼容
- **无新依赖**: 所有特性仅使用 stdlib