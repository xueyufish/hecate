## 1. Fan-out/Fan-in — 扇出/扇入

- [x] 1.1 向 DSL 节点类型添加 `FAN_OUT` 节点类型，带有 `branches: list[str]` 属性
- [x] 1.2 向 DSL 节点类型添加 `MERGE` 节点类型，带有 `reducer: str` 属性（"concat"、"select_first"、"custom"）和可选的 `reducer_fn: str`
- [x] 1.3 编译器：`detect_fan_out(node)` → 在 PregelRuntime 的 superstep 中将消息广播到所有分支
- [x] 1.4 编译器：`detect_merge(node)` → 配置合并行为（连接、选择第一个、自定义）
- [x] 1.5 运行时：在继续之前等待所有扇出分支完成（在同一个 superstep 中）
- [x] 1.6 运行时：扇出分支完成时触发合并
- [x] 1.7 测试：串联、select_first 和自定义 reducer

## 2. Subgraph — 子图

- [x] 2.1 向 DSL 节点类型添加 `SUBGRAPH` 节点类型，带有 `nodes`、`edges`、`input_mapping`、`output_mapping`
- [x] 2.2 编译器：`resolve_subgraphs(graph)` → 递归展平子图，连接通道映射
- [x] 2.3 编译器：在展平时输出通道重命名以避免冲突
- [x] 2.4 测试：单层子图、嵌套子图、通道映射

## 3. Multi-Agent Patterns — 多代理模式

- [x] 3.1 创建 `src/hecate/engine/patterns/__init__.py`
- [x] 3.2 实现 `hierarchical_pattern(manager_id, worker_ids, model) → dict`（manager 分配任务，workers 执行）
- [x] 3.3 实现 `handoff_pattern(agents, handoff_condition) → dict`（agent A 完成后，如果条件满足则 agent B 接替）
- [x] 3.4 实现 `pipeline_pattern(stages) → dict`（顺序阶段，每个阶段是 [agent_id, model]）
- [x] 3.5 实现 `broadcast_pattern(agent_id, targets) → dict`（扇出到所有目标）
- [x] 3.6 实现 `negotiation_pattern(agents, max_rounds=100) → dict`（agent 辩论并收敛）
- [x] 3.7 实现 `debate_pattern(agents, max_rounds=100) → dict`（agent 交换意见）
- [x] 3.8 测试：所有 6 种模式生成有效的 DSL 图

## 4. Typed Edges — 类型化边

- [x] 4.1 向 DSL 边添加 `EdgeType` 枚举：MESSAGE（默认）、CONDITIONAL、CONTROL、SUBSCRIBE
- [x] 4.2 编译器验证：MESSAGE 边连接任何非 CONDITION 节点；CONDITIONAL 边必须从 CONDITION 节点出发；CONTROL 边强制排序但不能有数据；SUBSCRIBE 边连接事件触发节点
- [x] 4.3 向后兼容性：没有 `type` 的现有边默认使用 MESSAGE
- [x] 4.4 测试：每种边类型的验证

## 5. Schema Update — Schema 更新

- [x] 5.1 使用新节点类型（FAN_OUT、MERGE、SUBGRAPH）和边类型更新 `graph-dsl.schema.json`
- [x] 5.2 添加 `branches`、`reducer`、`reducer_fn`、`input_mapping`、`output_mapping` 属性
- [x] 5.3 添加 `EdgeType` 枚举
- [x] 5.4 测试：新图形结构验证通过；旧图形结构仍然可以通过

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/`
- [x] 6.2 运行 `ruff format --check src/ tests/`
- [x] 6.3 运行 `mypy src/`
- [x] 6.4 运行 `python -m pytest tests/ -q`