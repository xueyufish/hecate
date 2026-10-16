# step5d G3 默认启用证据复核（g3-entry-evidence）

## 范围与结论

本记录对应演进计划 step5d 剩余项"G3 实际入口的多轮、拒绝、取消、断连/恢复及默认启用证据；复用现有开关和路由，旧循环限制须显式保留"。原有证据与 `step5d-g3-entry-conformance` 的恢复修正及入口证据在此统一登记。

**决策：全局默认 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 维持关闭。**HTTP 已补真实 TCP RST 中途断连与持久恢复证据；MCP 注册工具经协议会话覆盖多轮、拒绝及取消恢复；Workflow 证据限定为有部署身份的 Agent 子任务多轮与拒绝。按 `unified-chat-execution` 规格“逐入口证据积累、缺证据不切默认”，默认不翻转。仍需完成待发布入口/profile 的支持范围核对、翻转决策及实际放量审计核对；真实模型质量另归 step5e。

## 逐入口证据清单

### HTTP 入口（`/v1/chat/completions`、`/v1/agents/{id}/chat/completions`）

| 证据项 | 测试 | 结论 |
|---|---|---|
| 多轮工具调用（非流式） | `test_chat_engine_g3_entry.py::test_real_http_non_streaming_multi_round_tool_call` | 引擎路径完成，事件含配对 TOOL_CALL/TOOL_RESULT |
| 多轮工具调用（流式，中间迭代静默） | `test_chat_engine_g3_entry.py::test_real_http_streaming_multi_round_silent_intermediates` | 仅最终答案流式下发 |
| 审批拒绝传递 | `test_chat_engine_g3_entry.py::test_real_http_approval_denial_surfaces` | 工具不执行，拒绝以工具消息闭环，APPROVAL_ASKED/DECIDED 落事件 |
| 确定性策略拒绝（非审批） | `test_chat_engine_g3_entry.py::test_engine_deterministic_policy_denial_does_not_terminate` | 工具不执行，拒绝以工具消息闭环且对话不终止，无审批事件对 |
| 完成后的续轮/恢复 | `test_chat_engine_g3_entry.py::test_stream_disconnect_persists_state_and_resume_is_clean` | ASGITransport 在客户端读到首段前已缓冲完整服务端响应；证明完成后的快照和续轮，不证明中途断线 |
| 会话中取消（模型边界） | `test_chat_engine_g3_entry.py::test_engine_cancel_at_model_boundary_stops_and_resume_is_clean` | 边界取消检查生效，SSE 呈现 `execution_stopped`/`cancelled`，进行中轮恰好一次，恢复干净 |
| 跨请求多轮会话连续 | `test_chat_engine_g3_entry.py::test_engine_multi_turn_session_continuity` | 续轮恢复前轮工具环，亲缘保持 engine，前轮回执不重执行，配对跨轮完整 |
| 会话亲缘与放量覆盖 | `test_chat_engine_g3_entry.py::test_session_keeps_engine_path_after_override_flip` | 覆盖翻转后续轮不切换路径 |
| Task/Run 关联 | `test_chat_engine_g3_entry.py::test_http_entry_correlates_task_and_run` | 入口执行落 Task/Run |
| 回退路径钉住（开关关闭） | `test_chat_engine_g3_entry.py::test_flag_off_real_entry_keeps_direct_loop` | 直连循环响应语义不变，引擎事件日志零条目 |
| 停止语义（unknown/budget/iteration/结构化输出） | S12 场景（`test_s12_autonomous_multiturn.py`，双路径） | step5e 已交付，见机制矩阵与该 change 复核记录 |

### MCP `agent_chat` 入口

`tests/test_mcp/test_agent_chat_entry_parity.py` 的 `test_mcp_protocol_*` 使用 FastMCP Client 初始化协议会话并调用注册的 `session_create` / `agent_chat`，覆盖多轮工具配对、续轮真实回执、策略/审批拒绝与协议取消后的恢复。取消终态与拒绝记录从存储查询；不是直接调用私有 helper 的接线断言。拒绝审计与独立终态写入的组合以 PostgreSQL 验收，SQLite 跳过该组合；MCP 的网络传输、鉴权和安装/升级认证不由此授予。

### Workflow 入口

`tests/test_execution/test_workflow_child_adapter.py::test_child_agent_multi_round_tools_via_real_engine` 经真实 task-control、部署身份 Agent 子任务与工具网关验证多轮和策略/审批拒绝；新 PostgreSQL 会话查询子 Run、配对工具事件及拒绝回执，父任务经核实回调收敛，唤醒不重复派发。范围不包含独立 workflow 自身的 Run 身份，也不替代带工具子任务中途断连或跨进程故障认证。

## 放量记录核对

workspace 覆盖变更写 `CHAT_ENGINE_ROLLOUT_CHANGED` 放量记录（workspace、操作者、旧值→新值）由 `entry_routing.record_rollout_change` 承担，规格场景"覆盖变更写入放量记录"与亲缘用例覆盖读取路径。本轮未新增放量机制。

## 旧循环限制显式保留

开关关闭（默认）时直连循环作为回退路径保留，其边界在证据与文档中成文：

- 直连循环不经引擎事件日志——`test_flag_off_real_entry_keeps_direct_loop` 断言零引擎事件；因此也不具备引擎路径的持久回放保证（重放身份回执依赖治理账本，恢复语义弱于引擎路径）。
- 直连循环的限制与两路径机制差异见 [execution-mechanism-matrix](../design/execution-mechanism-matrix.md)；停止语义与重放身份的双路径对齐证据见 S12 场景（`openspec/changes/archive/2026-10-12-builtin-autonomous-execution/`）。
- 规格场景"关态行为有真实入口钉住"防止该限制被默认翻转 silently 抹去。

## 早期证据边界与当前处理

1. **跨轮回执内容曾为对账标记**：本 change 从会话日志回填实际结果，并拒绝把旧日志里的错误占位消息当作成功回执。不可恢复的写类动作显式停止，只读动作允许安全重执行。
2. **ASGI 测试传输不投递中途断连**：该限制保留，已有完成后续轮用例不升级为断连证据。本 change 另以真实 TCP RST 中途断连验证；普通 FIN、代理超时与其他网络故障尚未获该证据覆盖。
3. **真实模型维度不在本轮范围**：全部证据基于确定性桩；模型质量维度归 step5e 已登记的 skipped 真实轨。

## 验证

- `tests/test_channel/test_chat_engine_g3_entry.py`：确定性入口回归已通过；修正后的停止关联、输出校验与流式摘要证据见 `step5d-review-hardening` 的复核记录。
- 定向回归与门槛结果见 change 复核记录。

## 入口证据增补（`step5d-g3-entry-conformance`）

本节登记该 change 交付的逐入口证据增量与决策维持结论。

### 真实传输中途断连（HTTP 入口）

- `tests/test_channel/test_g3_real_tcp_disconnect.py`：uvicorn 真实 socket + 原始 socket 客户端以 SO_LINGER=0 RST 在执行中途断连。断开前工具轮已成功、最终模型调用仍受 gate 阻塞；不是读取完整响应后的关闭。
- 证据项：断连 Run 精确收敛 `cancelled`，独立 reader 查询唯一终态信封和同类投影；续轮的模型输入逐条核对真实工具内容且无重复副作用。缺失内容用例直接删除相同后端上的内容载体，保留成功回执，验证 `reconciliation_required` 的响应、投影和终态一致。
- 未配置 `HECATE_STEP6_POSTGRES_URL` 时仅运行文件 SQLite ORM 与内存事件/快照层，不宣称 PG 通过；配置后 ORM、事件与快照均使用 PostgreSQL，查询不依赖原执行器缓存。两层分别运行，不能用内存故障替身替代 PG 内容缺失验收。

### 回执恢复语义（跨轮/断连共用，行为变更）

- 恢复回填顺序固化为：通道历史 → 持久账本 `result_content` → **会话日志 messages 通道写（新增）** → 显式停止/重执行分流。
- succeeded-无内容按副作用类别分流：只读允许安全重执行（重获真实内容）；写类以 `reconciliation_required` 停止（原为 `[reconciliation required]` 占位文本喂给模型继续推理）。
- 配套产品修复：断连 checkpoint 保存加取消 shield；agent_state 快照在锁内读取并合并，保留 channel_state、intent、metadata 和对应 replay anchor，读失败跳过保存。仅更新 Agent 状态不提前消费尚未折叠的日志尾部。

### MCP `agent_chat` 入口

- 注册工具协议用例覆盖多轮、真实回执续接、策略与审批拒绝、取消终态与同会话恢复。拒绝时业务调用为零，拒绝回执持久可查，审批决定明确为不批准；取消通过 MCP 请求取消触发，不替换执行服务。

### Workflow 入口（部署身份 Agent 子任务）

- 新增真引擎用例覆盖多轮成功与策略/审批拒绝。provider 的最终答案依据收到的回执生成；每轮工具仅执行一次，子 Run 和事件在新 PG 会话中查询，父任务经核实回调收敛，唤醒后零重复。
- 该用例仅 PostgreSQL 参数化运行：task-control 内联派发 + 工具动作账本在 SQLite 上构成双同步连接单写者争用（`database is locked`），属存储层限制而非执行语义缺陷；未配置 URL 显式 skip。独立 workflow 自身的 Run/部署身份不在本证据内（另立设计）。

### 决策维持

全局默认 `CHAT_TOOL_LOOP_ENGINE_ENABLED` **维持关闭**。本轮入口证据的支持范围如上；安装/升级、独立 workflow 身份及其他网络故障不据此获认证。实际放量审计核对与翻转决策尚未完成；真实模型质量验收归 step5e。PG CI 已增加这些模块的执行命令，本地通过不冒充远程 CI 已运行。最终验证和修正记录见 [change 复核](../../openspec/changes/step5d-g3-entry-conformance/review.md)。
