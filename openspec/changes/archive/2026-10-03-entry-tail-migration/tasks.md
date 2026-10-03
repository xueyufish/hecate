# Tasks

## 1. A2A executor 迁移

- [x] 1.1 重写 `src/hecate/channel/a2a/server/executor.py` 执行路径：解析 agent 后经 `create_runtime_port` + agent 工具面（`_build_tool_registry`/`_load_agent_tools`）+ guardrail bundle + 共享 event store 构造 `EntryExecutionService(entry_name="a2a-chat")` 委托执行；删除 `llm_service` 直连；修正过期 docstring
- [x] 1.2 A2A correlation：`CorrelationInput(workspace_id=agent.workspace_id, agent_id=agent.id, goal=user_message[:200])`；关联缺失按 fail-open 记日志，不改变协议响应
- [x] 1.3 结果映射：非流式 dict 的 `content` 映射为 `Task.status.message` 与 `Artifact(name="response")`；completed/failed 状态映射与异常路径返回 `TaskState.FAILED` 保持现状

## 2. 定时任务 AgentExecutor 迁移

- [x] 2.1 修改 `src/hecate/ops/scheduling/executors.py` 的 `AgentExecutor`：签名增加任务行上下文入参（`workspace_id`/`agent_id`），`task_config["agent_id"]` 保留兼容回退；执行改经 `EntryExecutionService(entry_name="scheduled-agent")`，删除 `llm_service` 直连；返回 `{"status","result"}` 结构不变；模块注释更新（去除"直连 llm_service 与 A2A 同型"的过期说明）
- [x] 2.2 correlation：goal 取触发 message 前 200 字符，`user_id=None`；workspace 归属以任务行为权威来源

## 3. 测试

- [x] 3.1 A2A executor 真实入口回归（`tests/test_a2a/test_server/test_executor_entry.py`）：经 handler 真实调用执行——工具装配生效（事件日志含配对 TOOL_CALL/TOOL_RESULT）、Task/Run 按 agent workspace 登记、Task/Artifact 字段与状态枚举与迁移前基准一致（含 history 空列表的既有行为钉住）
- [x] 3.2 定时任务 AgentExecutor 回归：任务行 workspace/agent 上下文传入执行并登记 Task（含行上下文优先于 agent 行的权威性钉子）；`task_config` 兼容回退路径覆盖；失败路径返回 failed
- [x] 3.3 静态防回归：两处 executor 模块源码断言无 `llm_service.chat(`/`llm_service.chat_stream(` 直连调用（import 作为 provider seam 传入 runtime port 是允许的，与 IM 适配器同构）；`tests/test_layering_entry_imports.py` 扫描模块集加入 `channel/a2a/server`
- [x] 3.4 既有测试修正：`tests/test_services/test_scheduling/test_executors.py` 中钉住 llm 直连行为的断言改为钉住入口服务行为（`tests/test_a2a/` 原本无 executor 执行测试，新增文件覆盖）

## 4. 文档与验证

- [x] 4.1 更新 `docs/refactor/enterprise-agent-platform-evolution-plan.md` step5 注记："按入口清单迁移"项移除 A2A/定时任务 executor 未迁移登记，新增 `WorkflowExecutor`→studio 测试入口路径登记；step5d 注记更新为尾链切片完成（整体项保持未勾选，A2A 协议身份与调度接线缺口显式记录）
- [x] 4.2 全量验证：ruff check/format（src/、tests/、scripts 全量）、mypy src/ 605 文件零错误、`openspec validate entry-tail-migration --strict` 通过、受影响测试（test_a2a、test_services/test_scheduling、test_layering_entry_imports、test_execution、test_channel/test_chat_engine_g3_entry.py、test_mcp/test_agent_chat_entry_parity.py）401 通过 25 跳过
