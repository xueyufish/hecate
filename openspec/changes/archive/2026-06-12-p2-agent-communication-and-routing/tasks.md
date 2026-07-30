## 1. Engine Types & Data Model — 引擎类型与数据模型

- [x] 1.1 向 `engine/types.py` 添加 `ChannelAccess` 数据类，包含 `readable: set[str]` 和 `writable: set[str]` 字段
- [x] 1.2 向 `CompiledGraph` 数据类添加 `channel_access: dict[str, ChannelAccess]` 字段（默认空字典）
- [x] 1.3 向 `engine/types.py` 中的 `NodeConfig` 数据类添加 `routing_mode: str | None` 和 `routing_config: dict[str, Any] | None` 字段
- [x] 1.4 在 `engine/types.py` 中添加 `RoutingMode` StrEnum，值为 `CONDITION`、`INTENT`、`DYNAMIC`
- [x] 1.5 在 `engine/types.py` 中添加 `IntentPattern` 数据类，包含 `pattern: str` 和 `target: str` 字段

## 2. Graph DSL Schema & Parser — Graph DSL 模式与解析器

- [x] 2.1 更新 `schemas/graph-dsl.schema.json` — 向 CONDITION 节点配置属性添加 `routing_mode`（枚举：condition/intent/dynamic）和 `routing_config`（包含 intent_patterns、candidate_agents、routing_prompt、allow_repeated_speaker 的对象）
- [x] 2.2 更新 `schemas/graph-dsl.schema.json` — 向边 `trigger` 枚举值添加 `"dynamic_handoff"`
- [x] 2.3 更新 `engine/graph_dsl.py` 的 `parse_graph()` 以从 CONDITION 节点配置解析 `routing_mode` 和 `routing_config`
- [x] 2.4 更新 `engine/graph_dsl.py` 以验证 `routing_mode` 值 — 对未知模式抛出 `GraphValidationError`
- [x] 2.5 添加测试：使用意图路由配置的 parse_graph 产生正确的 NodeConfig
- [x] 2.6 添加测试：使用动态路由配置的 parse_graph 产生正确的 NodeConfig
- [x] 2.7 添加测试：使用无效 routing_mode 的 parse_graph 抛出 GraphValidationError
- [x] 2.8 添加测试：使用 dynamic_handoff 触发器的 parse_graph 产生正确的 Edge

## 3. Compiler Validation — 编译器验证

- [x] 3.1 向 `GraphCompiler` 添加 `_validate_channel_access()` 方法 — 遍历节点，对照状态通道检查 readable/writable，对不匹配记录 WARNING
- [x] 3.2 向 `GraphCompiler` 添加 `_validate_routing_config()` 方法 — 验证意图模式有 intent_patterns，动态模式有 candidate_agents，候选 agent 引用现有节点
- [x] 3.3 在 `compile()` 中从节点配置通道填充 `CompiledGraph.channel_access` 映射
- [x] 3.4 将两个新验证方法集成到 `compile()` 管道中（在现有验证之后，CompiledGraph 构建之前）
- [x] 3.5 添加测试：节点声明不在状态中的可读通道时编译器发出警告
- [x] 3.6 添加测试：节点声明不在状态中的可写通道时编译器发出警告
- [x] 3.7 添加测试：意图模式无 intent_patterns 时编译器抛出 GraphValidationError
- [x] 3.8 添加测试：动态模式无 candidate_agents 时编译器抛出 GraphValidationError
- [x] 3.9 添加测试：动态模式包含不存在的候选时编译器抛出 GraphValidationError
- [x] 3.10 添加测试：编译器正确填充 channel_access 映射
- [x] 3.11 添加测试：编译器接受有效的意图路由配置
- [x] 3.12 添加测试：编译器接受有效的动态路由配置

## 4. Runtime Channel Access Enforcement — 运行时通道访问执行

- [x] 4.1 向 `ChannelManager.read()` 添加可选参数 `node_id: str | None = None`
- [x] 4.2 向 `ChannelManager.write()` 添加可选参数 `node_id: str | None = None`
- [x] 4.3 在 `ChannelManager.read()` 中 — 当提供 `node_id` 时，对照已编译图谱的 channel_access 映射检查，对未声明访问记录 WARNING
- [x] 4.4 在 `ChannelManager.write()` 中 — 当提供 `node_id` 时，对照已编译图谱的 channel_access 映射检查，对未声明访问记录 WARNING
- [x] 4.5 从 PregelRuntime 的节点执行循环向 ChannelManager 调用传递 `node_id`
- [x] 4.6 添加测试：ChannelManager.read() 对未声明的通道访问记录警告
- [x] 4.7 添加测试：ChannelManager.write() 对未声明的通道访问记录警告
- [x] 4.8 添加测试：node_id 为 None 时 ChannelManager.read() 不发出警告
- [x] 4.9 添加测试：ChannelManager.read() 对已声明的通道访问不发出警告

## 5. Routing Mode Evaluation Engine — 路由模式评估引擎

- [x] 5.1 向新的 `engine/routing.py` 添加 `evaluate_condition_routing()` 函数 — 根据 routing_mode 分发
- [x] 5.2 实现 condition 模式评估（委托给现有表达式求值器）
- [x] 5.3 实现 intent 模式评估 — 遍历 intent_patterns，对输入进行正则匹配，找到第一个匹配时返回目标
- [x] 5.4 实现 intent 模式 LLM 后备 — 当无模式匹配时，使用 routing_prompt 调用 `EnginePort.llm_invoke()`
- [x] 5.5 实现 dynamic 模式评估 — 使用 candidate_agents 列表和 routing_prompt 调用 `EnginePort.llm_invoke()`
- [x] 5.6 实现 dynamic 模式 `allow_repeated_speaker` — 在 LLM 调用前从候选中过滤上一个发言者
- [x] 5.7 实现 dynamic 模式响应验证 — 对照 candidate_agents 检查 LLM 响应，回退到 "default" 目标
- [x] 5.8 将路由评估集成到 PregelRuntime 的条件节点执行路径中
- [x] 5.9 添加测试：意图模式匹配模式返回正确目标
- [x] 5.10 添加测试：意图模式无模式匹配且 LLM 后备返回 LLM 分类的目标
- [x] 5.11 添加测试：意图模式无模式匹配且无 routing_prompt 返回 "default"
- [x] 5.12 添加测试：动态模式从 LLM 响应返回有效 agent
- [x] 5.13 添加测试：动态模式无效 LLM 响应回退到 "default"
- [x] 5.14 添加测试：动态模式 allow_repeated_speaker=false 排除上一个发言者

## 6. Dynamic Handoff Support — 动态 Handoff 支持

- [x] 6.1 更新编译器中的 `_validate_handoff_edges()` 以同时验证 `dynamic_handoff` 触发器边
- [x] 6.2 更新 handoff 工具注入逻辑 — 当边触发器为 `dynamic_handoff` 时，注入包含多个目标候选的 `handoff_to_agent`
- [x] 6.3 更新 handoff 工具执行 — 对照允许的候选列表验证目标，对无效目标返回错误
- [x] 6.4 添加测试：动态 handoff 边触发包含多个目标的工具注入
- [x] 6.5 添加测试：动态 handoff 无效目标返回错误
- [x] 6.6 添加测试：动态 handoff 循环检测正常工作

## 7. Frontend — Channel Access Summary — 前端 — 通道访问摘要

- [x] 7.1 在 `web/src/components/workflow/config-panel.tsx` 的 agent 节点配置面板中添加通道访问摘要部分
- [x] 7.2 按类型分组的可读/可写通道显示，带广播参与高亮
- [x] 7.3 对无通道声明的节点显示"未配置通道访问"消息
- [x] 7.4 为与其他 agent 共享的 TOPIC 通道添加广播图标

## 8. Frontend — Routing Mode Config Panel — 前端 — 路由模式配置面板

- [x] 8.1 向 condition 节点配置面板添加路由模式选择器（Condition/Intent/Dynamic）
- [x] 8.2 实现 Intent 模式 UI — 带正则输入和目标节点选择器的意图模式行（添加/删除）
- [x] 8.3 实现 Intent 模式 UI — 可选的 routing prompt 文本域
- [x] 8.4 实现 Dynamic 模式 UI — 候选 agent 多选、routing prompt 文本域、allow_repeated_speaker 开关
- [x] 8.5 在变更时将 routing_mode 和 routing_config 持久化到图谱 DSL 节点数据
- [x] 8.6 Condition 模式（默认）仅显示现有表达式字段

## 9. Frontend — Dynamic Handoff Edge — 前端 — 动态 Handoff 边

- [x] 9.1 在 `edge-type-selector.tsx` 中的边类型选择器添加"动态 Handoff"选项
- [x] 9.2 使用独特样式渲染动态 handoff 边（紫色虚线 + 闪光图标）
- [x] 9.3 创建动态 handoff 边时支持多目标选择

## 10. Verification — 验证

- [x] 10.1 运行 `ruff check src/hecate/ tests/` — 0 错误
- [x] 10.2 运行 `ruff format --check src/ tests/` — 0 错误
- [x] 10.3 运行 `mypy src/` — 0 错误
- [x] 10.4 运行 `python -m pytest tests/test_engine/ -q` — 全部通过（495 通过）
- [x] 10.5 在 web/ 中运行 `npx tsc --noEmit` — 0 新错误（dsl-bridge.test.ts 中有 1 个预先存在的错误）
