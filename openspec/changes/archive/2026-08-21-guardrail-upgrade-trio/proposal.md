## Why

Roadmap Sprint 7 "Completed-Feature Upgrades" 三项守卫升级（1.3.5i E3 瀑布中间件链、1.3.4 HITL fail-closed 审批、9.4 内容感知工具门控）全部就绪待做：依赖的 1.3.19 事件日志已交付，ADR-030 已预写下游接缝契约，且审计报告将 1.3.4 + 9.4 列为仅剩的 P1 档发布阻塞项。

但探索验证发现比 roadmap 假设更严重的现状：**整条安全栈从未接入生产执行路径**——`ToolWorker` 构造时不传 `access_policy`/`approval_callback`（`execution_service.py:587`）、`create_security_hooks` 工厂全库零调用者（`AgentModel.guardrail_config` 是死配置）、`ToolPolicyPipeline` 仅存在于测试、Pregel interrupt 机制无生产者。生产 chat 主干路径（带工具的 agent，路径 A 直连循环）更是绕过 Pregel，连事件日志都没有。组件已造好且有单测，但线从未插上。本变更必须先"点亮"再"升级"，否则三项升级做完仍是空转。

## What Changes

按四层推进，前一层是后一层的地基：

- **T0 点亮（接线）**：`ToolAccessPolicy` + 审批回调接入 `WorkflowExecutionService` 构造的 `ToolWorker`；`create_security_hooks(agent.guardrail_config)` 接入 LLM/工具 hook 槽位；路径 A 直连循环（`_chat_with_tools`）接入同一套门控；修复 1.3.19 三个遗留缺陷（`TOOL.PAIRING` 不变式读错 payload key、`loginvariants.run_all()` 无调用者、`TOOL_CALL`/`TOOL_RESULT`/`LLM_RESPONSE` 事件缺 `log_schema_version`）。
- **T1 瀑布中间件链（1.3.5i E3）**：4 个扁平 hook 槽位升级为有序 `next()` 委派链（stages：agent/pre-step、agent/request、tools/pre-execute、execute、post-execute、result）；链语义（顺序、短路、单调）固定在引擎内核（ADR-029/030 约束），旧四类 hook ABC 通过适配器成为单 stage；`LLMWorker`、`ToolWorker`、`AgentExecutionPort` 与路径 A 直连循环共用同一链组件；per-agent scope 过滤以 `agent.guardrail_config` 为配置面。修复 SANITIZE 缺 `modified_data` 时静默降级为 ALLOW 的语义缺陷。
- **T2 审批兜底（1.3.4）**：新增 `APPROVAL_ASKED` / `APPROVAL_DECIDED` / `TURN_START` / `TURN_END` 事件类型（增量扩展，旧读取端降级为 CUSTOM）；审批对 TURN 封闭；无应答者 → 拒绝（fail-closed）；授权只生效一次（once-only，`ApprovalDecision.scope` 的 session/project/global 语义明确排除本次升级的自动授予路径）；`ApprovalRecordModel` 从孤立表改造为事件日志的可重建投影（服务审批队列查询，不做双写）。
- **T3 内容感知门控（9.4）**：基于 stdlib shlex 的 shell 命令静态分析（管道/命令链分解、命令替换 `$()` 检测、编码包装识别），超越现有 22 条 fnmatch glob 的单参数匹配；注册 `MONOTONIC.DENIAL` 不变式（guards 只能拒绝、复活即 bug、fail-stop）；发射已定义未使用的 `CHANNEL_WRITE_REJECTED` 事件；删除 `ModeLayer.override_decision` 的 AUDIT 模式 DENY→ALLOW 复活路径。

**BREAKING**：`ModeLayer.override_decision` 方法移除（当前无生产调用者，实际破坏面为零）；`LLMWorker`/`ToolWorker` 单 hook 构造参数标记 deprecated（保留兼容适配）。

## Capabilities

### New Capabilities
- `guardrail-middleware-chain`: 有序瀑布中间件链——引擎内核链机制（stage 顺序、BLOCK 短路、SANITIZE 传递、scope 过滤）、旧 hook ABC 的 stage 适配器、全执行路径（Pregel workers + 路径 A 直连循环）的统一守卫调用契约。

### Modified Capabilities
- `execution-security`: 工具门控升级——新增单调拒绝不变式（deny 不可复活）；审批升级为 fail-closed（无应答者拒绝、once-only 授权、事件审计对）；门控从"可接线"变为"生产路径强制生效"（路径 A + Pregel 路径）；`ApprovalRecordModel` 语义变更为事件投影。
- `granular-tool-security`: 危险模式检测从单参数 fnmatch glob 升级为 shell 静态分析（管道/命令链分解、命令替换检测、混淆变体识别）。
- `eventstore`: 事件类型扩展（`APPROVAL_ASKED`/`APPROVAL_DECIDED`/`TURN_START`/`TURN_END`）；`CHANNEL_WRITE_REJECTED` 从保留槽位变为实际发射；会话语义层事件补齐 `log_schema_version`；`LogInvariants` 注册表接入执行路径（`run_all()` 接线 + `TOOL.PAIRING` key 修复）。

## Impact

- **代码**：`engine/`（新中间件链模块、`guardrail.py` 适配器、`tool_access.py` shell 分析、`loginvariants.py`、`eventstore.py` 枚举、`tool_worker.py`/`llm_worker.py` 链接入）、`services/workflow/execution_service.py`（构造接线）、`services/orchestration/agent_execution_port.py`、`api/v1/chat.py`（路径 A 接入链）、`services/security/hooks/`（适配 stage）、`policy_layers.py`（删除复活路径）、审批投影维护组件。
- **API**：无 REST 接口变更；事件流新增 4 种事件类型（增量，向后兼容）。
- **依赖**：零新增第三方依赖（shell 分析用 stdlib `shlex`，符合 engine 层零外部依赖约束）。
- **边界（非目标）**：路径 A 收编进 Pregel（引擎内子图建模，ADR-030 长期方向）不在本变更；`HookConfigModel`/session events 脚手架接活另立变更；审批的 interrupt 化（挂起等人）不在本变更；审批事件对与单调拒绝不变式仅在 Pregel 路径完整生效（路径 A 至少跑通门控链，差异会在文档中显式标注）。
