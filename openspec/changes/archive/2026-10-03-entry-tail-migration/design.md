# Design

## Context

step5d 第一切片已交付 `EntryExecutionService`（`src/hecate/execution/entry_service.py`）：入口模块只做协议适配，执行装配（`WorkflowExecutionService` over 共享装配）与 Task/Run 关联（`TaskRunRegistry`，fail-open）发生在入口服务内。四条已迁移链的调用样板：

- HTTP chat（`channel/api/v1/chat.py`）：请求作用域 db，`create_runtime_port` + agent 工具面 + guardrail bundle + session_state checkpoint；
- IM 适配器（`core/composition/im_entry.py`）：后台自开 `async_session_factory` session，无请求上下文时的装配样板；
- MCP、评估：各自装配后委托同一入口服务。

未迁移的两条尾链与样板的差距：

- A2A executor（`channel/a2a/server/executor.py`）：请求作用域 db 可用（`POST /a2a/` → `A2ARequestHandler(db)` → executor），但执行直连 `llm_service.chat`（仅 persona+message，无工具/guardrail/事件/checkpoint/关联）；agent 选择为全局第一个非删除 agent；docstring 过期宣称走 `WorkflowExecutionService`。
- 定时任务 `AgentExecutor`（`ops/scheduling/executors.py`）：自开 session，同样直连 `llm_service.chat`；`ScheduledTaskModel` 行上有 `workspace_id`/`agent_id` 可供关联；该 executor 目前未被 `manager._execute_task` 接线（manager 只记录执行行），registry 仅为后续接线准备。
- 定时任务 `WorkflowExecutor` 走 studio `WorkflowTestRunner`（自带 GraphCompiler+PregelRuntime 装配），不经 `llm_service`，不在本次绕过清单内。

非流式执行结果为 dict，assistant 文本在 `result["content"]`（与 HTTP chat 渲染一致）。

## Goals / Non-Goals

**Goals:**

- A2A executor 与定时任务 `AgentExecutor` 的执行路径经 `EntryExecutionService` 发起，`llm_service` 直连删除；
- 两条链获得与其余入口一致的装配语义（工具面、guardrail bundle、共享 event store）与 Task/Run 关联（workspace 归属取所选 agent / 任务行）；
- A2A 协议响应与定时任务 executor 返回值结构保持兼容；
- 分层测试把 `channel/a2a/server` 纳入入口层扫描。

**Non-Goals:**

- 不接线 `manager._execute_task` → executor registry（调度真实执行、结果回写 `result_summary` 属 step6 持久任务）；
- 不做 A2A 协议级身份（per-agent card、按调用方选 agent）——保持现有"第一个非删除 agent"选择语义；
- 不迁移 `WorkflowExecutor`/`WorkflowTestRunner`（studio 测试入口，演进方案单独登记）；
- 不引入新的运行时扩展点（复用既有 seam，符合 `runtime-pluggability` 规则）。

## Decisions

### D1: A2A executor 采用 IM 适配器同构装配，而非抽公共装配函数

executor 已持有请求作用域 db，直接在 executor 内完成：解析 agent（沿用现有选择语义）→ 有工具时 `_build_tool_registry`/`_load_agent_tools` + `assemble_guardrails` + 进程级共享 event store（与 `im_entry._get_shared_event_store` 同源）→ `create_runtime_port` → `EntryExecutionService(entry_name="a2a-chat")`。

- 备选：把 IM/A2A 的装配抽成公共函数放入 execution 域。否决理由：两处上下文不同（IM 无请求 db、A2A 有），当前只有两个消费点，过早抽象违背"无第二实现/具名消费者不新增扩展点"纪律；待第三处出现再抽。
- 与 IM 的差异：A2A 使用请求 db（不自开 session），不设 checkpoint_store（A2A 会话状态由 `DatabaseTaskStore` 承载，与 HTTP chat 的 session_state store 语义不同，不强行复用）。

correlation：`CorrelationInput(workspace_id=agent.workspace_id, agent_id=agent.id, user_id=None, goal=user_message[:200])`——修正现状"无 workspace 归属"（原实现连 agent 行的 workspace 都未用），但 agent 选择语义不变（选择缺口属协议设计，见 Non-Goals）。

### D2: 定时任务 AgentExecutor 用任务行字段做关联，保留 task_config 兼容

执行仍自开 `async_session_factory` session（后台无请求上下文，样板=IM）。executor 签名增加任务行上下文入参（`workspace_id`/`agent_id`，调用方可传 `ScheduledTaskModel` 行值）；`task_config["agent_id"]` 保留为兼容回退。correlation goal 取触发 message 前 200 字符；`user_id=None`（系统触发，身份链 initiator 为空、principal 为 agent，由 `EntryExecutionService._identity_chain` 既有规则处理）。

- 备选：不改签名，只从 `task_config` 取。否决理由：`ScheduledTaskModel` 行才是 workspace 归属的权威来源，task_config 由用户填写、不可信为主数据。

### D3: 结果映射保持两层各自协议形状

A2A：`entry.execute(...)` 非流式返回 dict → `content` 映射为 `Task.status.message.parts[0].text` 与 `Artifact(name="response")`，状态映射 completed/failed 与现状一致；异常路径仍返回 `TaskState.FAILED`（关联缺失不改变协议结果——fail-open 契约）。定时任务：`{"status": "success", "result": content}` 结构不变。

### D4: 分层扫描扩展与静态防回归

`tests/test_layering_entry_imports.py` 的扫描模块集加入 `channel/a2a/server`；新增静态断言（针对两处 executor 模块源码）：不得出现 `llm_service` import——比"不得内联装配"更具体地钉住本次迁移对象。真实入口回归不 mock `EntryExecutionService`，仅 stub provider 边界（与 `test_chat_engine_g3_entry.py` 同一纪律）。

### D5: 计划文档登记收敛

演进方案 step5 的"按入口清单迁移"注记更新：未迁移列表移除 A2A executor 与定时任务 executor（llm_service 绕由），新增登记 `WorkflowExecutor`→`WorkflowTestRunner` 的 studio 测试入口路径为剩余条目；step5d 注记更新为"第二切片（尾链）完成"。

## Risks / Trade-offs

- **装配开销**：两条链从单次 LLM 调用变为完整装配执行（工具解析、guardrail bundle），单次执行时延上升。可接受：这正是迁移目的（获得 guardrail/审计/关联）；A2A/定时任务均非最低时延敏感路径（HTTP 无 KB 直连路径不受影响）。
- **A2A agent 选择语义保留**：仍选全局第一个 agent，多 workspace 下行为不理想。已显式登记为协议设计缺口（Non-Goals），spec 只约束"关联归属取所选 agent 的 workspace"，不掩盖缺口。
- **correlation fail-open**：登记失败时执行照常、结果显式标记缺失。与既有四链一致；A2A 协议响应不塞入关联字段（保持协议兼容，缺失只记日志/结果 dict），HTTP chat 的 `hecate_correlation` 字段模式不引入 A2A。
- **定时任务 executor 未接线**：迁移后的 AgentExecutor 仍不被 manager 调用，"入口迁移"收敛的是执行路径本身；真实调度执行留 step6。测试直接驱动 executor（不经 manager），并在计划文档明确登记该状态，避免误读为"定时任务已可执行"。
