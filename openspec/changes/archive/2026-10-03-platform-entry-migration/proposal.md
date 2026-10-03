# Proposal: platform-entry-migration

## Why

step5a–5c 已交付共享装配、独立发行包与只读宿主,但平台侧入口仍在各自模块内联构造 `WorkflowExecutionService` 且装配不一致(MCP `agent_chat`/`session_resume` 未接 event_store/checkpoint,HTTP 路径接全),入口间工具事件与恢复语义存在漂移;引擎聊天路径只有 patch 掉服务的分支测试,G3(入口与引擎收敛)缺真实入口证据,无法按 workspace 放量;入口执行不登记 Task/Run,step5 验收"同一 Agent 从不同入口执行,能关联统一 Task/Run"当前不成立。

## What Changes

- 新增平台入口执行应用服务(`src/hecate/execution/`):统一消费平台 adapter(`WorkflowExecutionService`,内含共享装配),经 `TaskRunRegistry` 登记入口执行的 Task/Run 与 conversation 链接;同步与流式接口是该执行上的等待/订阅视图,不引入独立执行生命周期。
- 按入口清单迁移第一组调用链,每组一次切换并带兼容测试:HTTP chat(`channel/api/v1/chat.py`、`agents.py`)→ MCP(`tools/mcp/server.py` 的 `agent_chat`/`session_resume`,补齐装配缺口)→ IM 注入与评估 workflow 执行(`ops/evaluation/engine.py`)。
- 补 G3 实际入口测试:经真实 HTTP/SSE 入口的流式/非流式、多轮工具调用、审批拒绝、断线/恢复与取消;核对 tool call/result 配对与拒绝记录传递。不重复实现聊天子图。
- `CHAT_TOOL_LOOP_ENGINE_ENABLED` 支持 workspace 级覆盖:按 workspace 切流,会话亲缘(活跃会话保持既有路径);放量以显式记录留痕(actor/时间/旧→新),默认值保持 `false`。
- Pregel 原始流事件映射为平台契约 `EventEnvelope`(task/run 引用、序号、游标),原始事件作为后端详情保留;OpenAI 风格响应与 SSE 转换留在适配层,客户端可见字段不携带 backend 专属内容。
- 分层测试约束:被迁移的入口层模块禁止新增 `PregelRuntime`、`GraphCompiler` 具体导入。
- 发布契约/依赖矩阵:内置执行包(hecate-runtime)与宿主(hecate-runner)单独升级的支持窗口显式成文。
- 计划文档同步:补勾 step5c 两项(证据 #208)。

**非目标**:A2A executor 与定时任务 executor 的迁移(现完全绕过执行服务,属后续切片,本次只登记);Task 工作流状态机、持久化 outbox 与控制命令(step6);全局默认开关翻转(由放量记录决策);G2 未关闭前不对新路径授予可靠副作用恢复保证。

## Capabilities

- **New Capabilities**:`platform-entry-execution` — 平台入口执行应用服务:入口统一消费共享装配、Task/Run 关联、事件映射、workspace 切流与放量记录、分层与升级边界。
- **Modified Capabilities**:`unified-chat-execution` — 增加 workspace 级切流与真实入口证据要求(现有需求只定义了全局开关行为)。

## Impact

- 代码:`src/hecate/execution/`(新入口服务)、`src/hecate/channel/api/v1/chat.py`、`agents.py`、`src/hecate/tools/mcp/server.py`、`src/hecate/channel/im/message_bus.py`(注入对象)、`src/hecate/ops/evaluation/engine.py`、`src/hecate/core/composition/` 装配、`src/hecate/core/config.py`(workspace 覆盖读取)。
- 契约:复用既有 `contracts/execution/` 类型与 JSON Schema,不新增字段;OpenAPI 样例保持 v0_1。
- 测试:`tests/test_runtime/test_chat_engine_convergence.py`(真实入口扩展)、`tests/test_execution/`、`tests/test_services/test_workflow/` 兼容回归。
- 文档:`docs/refactor/enterprise-agent-platform-evolution-plan.md`(5c 勾选)、发布矩阵文档。
