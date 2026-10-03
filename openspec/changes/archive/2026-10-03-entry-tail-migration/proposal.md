# Proposal

## Why

step5d 第一切片（change `platform-entry-migration`）已将 HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行四条链迁入统一的 `EntryExecutionService`，并在演进方案中把 A2A executor 与定时任务 executor 登记为"仍直连 `llm_service.chat` 的未迁移绕过路径"。这两条尾链目前：无工具装配、无 guardrail、无事件/checkpoint 存储、无 Task/Run 关联；A2A executor 还全局选取第一个 agent（无 workspace 归属），其 docstring 过期地宣称"委托 WorkflowExecutionService、guardrails/tracing/audit 全部生效"，与实现不符，且直接违反 `a2a-protocol` spec 的执行路由需求。迁移它们是收敛 step5d 入口清单、让"入口统一消费一个执行应用服务"从带例外变为无条件成立的收口动作。

## What Changes

- A2A executor（`src/hecate/channel/a2a/server/executor.py`）改为经 `EntryExecutionService`（entry_name `a2a-chat`）执行：与 IM 适配器同构的装配（runtime port、agent 工具面、guardrail bundle、共享 event store），correlation 使用所选 agent 行的 `workspace_id`/`agent_id`；修正过期 docstring；执行结果映射回 A2A `Task`/`Artifact`，协议响应 schema 保持兼容。
- 定时任务 `AgentExecutor`（`src/hecate/ops/scheduling/executors.py`）改为经 `EntryExecutionService`（entry_name `scheduled-agent`）执行：后台自开 session（`async_session_factory`），correlation 使用任务行的 `workspace_id`/`agent_id`（保留 `task_config["agent_id"]` 兼容入参），goal 取触发消息；`llm_service.chat` 直连删除。
- 两条链各附真实入口回归：guardrail/工具装配生效、Task/Run 关联登记、无 `llm_service` 直连的静态断言；A2A 协议响应 schema 与迁移前基准一致。
- 分层测试将 `channel/a2a/server` 纳入入口层扫描范围（迁移后它同样是入口模块，不得新增引擎具体导入）。
- 演进方案 step5 注记更新：入口清单的"未迁移"列表清空 `llm_service.chat` 绕过项；`WorkflowExecutor`（经 studio `WorkflowTestRunner` 的测试入口路径，非 `llm_service` 绕过）单独登记为后续条目。

不做（显式排除）：

- 调度器 `manager._execute_task` 与 executor registry 的接线（让定时任务真正执行）——属 step6 持久化任务范围；本 change 只迁移既有 executor 的执行路径。
- A2A 协议级身份设计（per-agent card 绑定、按调用方选择 agent）——协议层当前无 workspace/agent 上下文是既有缺口，本 change 仅把关联归属修正为"所选 agent 的 workspace"，不改变 agent 选择语义。
- `WorkflowExecutor`→`WorkflowTestRunner` 路径的迁移（studio 测试入口，单独登记，不在本次绕过清单内）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `platform-entry-execution`: "平台入口统一消费一个执行应用服务"需求修改——迁移链清单加入 A2A executor 与定时任务 agent executor，未迁移绕过路径的登记条款改为"不得存在未登记的绕过路径"；"每次入口迁移附真实入口回归"需求中切片完成的表述同步更新。
- `a2a-protocol`: "A2A 任务经执行管线路由"需求修改——过期措辞（`EnginePort.agent_execute()`）更新为经平台入口执行服务/共享装配，并补充 Task/Run 关联与 guardrail 生效的可验证语义。

## Impact

- 代码：`src/hecate/channel/a2a/server/executor.py`（重写执行路径）、`src/hecate/ops/scheduling/executors.py`（AgentExecutor 执行路径）；不改 A2A handler/JSON-RPC 协议层、不改调度 manager/registry 结构。
- 测试：`tests/test_a2a/`（executor 相关）、`tests/test_services/test_scheduling/test_executors.py`、`tests/test_layering_entry_imports.py`（扫描范围扩展）。
- 文档：`docs/refactor/enterprise-agent-platform-evolution-plan.md` step5 注记。
- 兼容性：A2A `Task`/`Artifact` 响应字段与状态不变；定时任务 `AgentExecutor` 返回值结构（`{"status", "result"}`）不变；执行时延因共享装配略增（新増工具/guardrail 装配），无接口级 breaking。
