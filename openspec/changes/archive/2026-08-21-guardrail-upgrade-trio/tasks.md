# Tasks: guardrail-upgrade-trio

## 1. T0 — 事件地基修复（1.3.19 遗留缺陷 + 新事件类型）

- [x] 1.1 `EventType` 枚举新增 `APPROVAL_ASKED` / `APPROVAL_DECIDED` / `TURN_START` / `TURN_END`；验证未知值读取仍降级 `CUSTOM`（`eventstore.py` + 测试）
- [x] 1.2 `TOOL_CALL` / `TOOL_RESULT` payload 增补 `tool_call_id` 与 `log_schema_version`；`LLM_RESPONSE` 补 `log_schema_version`（`tool_worker.py` / `llm_worker.py` + 测试）
- [x] 1.3 修复 `TOOL.PAIRING` 不变式 key 错配：改为按 `tool_call_id` 配对 `TOOL_CALL`/`TOOL_RESULT`，与 worker 实际发射结构一致（`loginvariants.py` + 测试）
- [x] 1.4 `loginvariants.run_all()` 接线到 `_restore_from_checkpoint`（fold 之后执行，违例 fail-stop）（`pregel.py` + 测试）
- [x] 1.5 Pregel 运行时在回合首尾发射 `TURN_START` / `TURN_END`（payload 含 `log_schema_version`）（`pregel.py` + 测试）

## 2. T0 — 生产路径接线（点亮现有组件）

- [x] 2.1 新建 services 层装配门面 `services/security/guardrail_assembly.py`：从 `agent.guardrail_config` 调 `create_security_hooks`、从 `ToolPolicyModel`/`ToolPolicyRuleModel` 加载 `ToolRule` 列表、构造 `ToolAccessPolicy`（+ 单测）
- [x] 2.2 `WorkflowExecutionService` 构造 `ToolWorker` 时传入 `access_policy` 与审批回调（T2 前先接占位回调：无应答者 → 拒绝）（`execution_service.py` + 集成测试）
- [x] 2.3 chat 路径 A `_chat_with_tools` / `_stream_chat_with_tools` 接入门控：工具执行前走同一 `ToolAccessPolicy` 评估（`api/v1/chat.py` + 测试）
- [x] 2.4 决策审计归属透传：`_check_access` 上下文补 `session_id`/`agent_id`/`workspace_id`/`on_behalf_of_user`，`ToolDecisionModel` 记录可查归属（`tool_worker.py` + 测试）
- [x] 2.5 端到端验证：路径 A 与 Pregel 路径上 `risk_level="critical"` 无审批的工具调用均被拒绝（fail-closed 一致性测试）

## 3. T1 — 瀑布中间件链（1.3.5i E3）

- [x] 3.1 新建 `engine/middleware.py` 链内核：phase 枚举（`AGENT_PRE_STEP`/`AGENT_REQUEST`/`LLM_RESPONSE`/`TOOL_PRE_EXECUTE`/`TOOL_EXECUTE`/`TOOL_POST_EXECUTE`/`TOOL_RESULT`）、stage 接口（`handle(ctx, call_next)`）、顺序执行、BLOCK 短路（含 stage 标识与 reason）、SANITIZE 传递、SANITIZE 缺 `modified_data` 视为违例按 BLOCK（+ 内核单测覆盖全部语义）
- [x] 3.2 `HookStageAdapter`：四类旧 hook ABC 适配为单 stage，matcher 工具名过滤语义保留（+ 适配器测试，现有三个 security hook 实现不改写直接入链验证）
- [x] 3.3 `LLMWorker` / `ToolWorker` / `AgentExecutionPort` 接链：真实执行作为终点 handler；旧单 hook 构造参数包装为单 stage 链并标 deprecated（+ 兼容性测试：旧参数路径行为不变）
- [x] 3.4 装配门面升级：按 `agent.guardrail_config` 装配期过滤 stage（未启用的不进链），构造各 phase 的链（+ scope 过滤测试）
- [x] 3.5 路径 A 直连循环接链：LLM 请求与工具执行处调用与 Pregel 路径相同的链组件（+ 双路径一致性测试）
- [x] 3.6 stage 决策审计：链产出每个 stage 的决策记录，BLOCK 必有留痕（接 `decision_emitter` 或事件）（+ 测试）
- [x] 3.7 `BudgetEnforcementHook` 修正为合法 `PreLLMHook` 实现或移除（消除接口不符死代码）

## 4. T2 — 审批兜底（1.3.4 fail-closed）

- [x] 4.1 新建 `services/security/approval.py`：`ApprovalCallback` 实现，`request_approval` 前后发射 `APPROVAL_ASKED` / `APPROVAL_DECIDED`；无应答者时产出完整审计对（decided=denied, reason="no answerer"）（+ 测试）
- [x] 4.2 TURN 封闭：审批事件对写入时校验处于 `TURN_START`/`TURN_END` 区间；路径 A 循环入口/出口发射 TURN 事件（+ 跨回合违例测试）
- [x] 4.3 once-only 消费：ONCE 授予按 `tool_call_id` 消费，不可重放；SESSION+ 范围缓存仅认持久 `APPROVAL_DECIDED` 记录（+ 测试覆盖规格三场景）
- [x] 4.4 `ApprovalRecordModel` 投影化：审批组件双写（先事件后投影），投影含重建函数（从事件日志重放审批事件重建表）（+ 重建测试）
- [x] 4.5 会话恢复重建：从日志尾部重建会话级授予集与拒绝集（拒绝集供 T3 运行时单调拒绝使用）（+ 恢复测试）
- [x] 4.6 替换 2.2 的占位回调为正式审批组件，路径 A/B 统一接入（+ 端到端审批流测试）

## 5. T3 — 内容感知门控 + 单调拒绝（9.4）

- [x] 5.1 新建 `engine/shell_analysis.py`：引号感知操作符分解（`|`/`&&`/`||`/`;`/换行）→ `shlex.split` 分词 → flag 簇规范化 → `$()`/反引号/`eval`/`sh -c` 递归检查（深度上限）→ 解析失败降级为整串 glob（+ 单测覆盖规格全部场景：管道后段、命令链、命令替换、空白/flag 变体、无害命令、降级路径）
- [x] 5.2 `tool_access.py` 危险模式匹配接入 shell 分析（shell 类工具先分解后逐段匹配），`DANGEROUS_PATTERNS` 增补管道后段执行外部内容、命令替换类规则（+ 匹配测试）
- [x] 5.3 运行时单调拒绝：`ToolWorker` 与路径 A 门控评估前查会话拒绝集（key=`tool_call_id`），已拒绝调用直接 DENY 不再重评估/重询问（+ 复活阻断测试）
- [x] 5.4 删除 `ModeLayer.override_decision`（DENY→ALLOW 复活路径）及其测试引用（+ 不变量测试：AUDIT 模式遇 DENY 保持 DENY）
- [x] 5.5 门控/守卫拒绝时发射 `CHANNEL_WRITE_REJECTED`（audit-only，fold 跳过验证）（+ fold 测试）
- [x] 5.6 `MONOTONIC.DENIAL` 不变式注册：拒绝记录后出现同 `tool_call_id` 的 `TOOL_CALL` 执行即违例（+ 不变式测试与正常日志零违例测试）

## 6. 验证与收尾

- [x] 6.1 全量校验：`ruff check` + `ruff format --check` + `mypy src/` + `python -m pytest tests/ -q` 全绿（ruff/format/mypy 全过；pytest 分批合计 3674 passed：engine+services+api 2995 + integration 18 + 其余目录 661，修复 1 个测试间注册顺序耦合后全绿）
- [x] 6.2 文档同步（核心项已完成）：`concepts/guardrails.md`（链叙事+双路径覆盖节）、`reference/event-catalog.md`（引擎事件日志节：5 新事件+4 不变式）、`reference/extension-points.md`（第 10/23 节链化+fail-closed 默认）、AGENTS.md 扩展点表（middleware 链/拒绝追踪器/shell 分析器）、`docs/design/engine-design.md` 守卫章节（E3 链内核+审批接线+内容感知+单调拒绝四小节）、ADR-030 接缝表落地标注。收尾批次：`security-architecture.md`（hook 表链化+注册节改装配门面+fail-closed/内容感知小节）、`extension-architecture.md`（目录树 7 新模块+第 10 节链化）、`threat-model.md`（实现路径清单 6 新条目）、`concepts/engine.md`（TURN/审批对/不变式段）、`tutorials/05`（chain note）、`tutorials/06`（tool-approval vs workflow-interrupt 区分 note）、`reference/glossary.md`（3 新词条+4 词条更新）。其余 ~40 个纯中性提及文档维持原文（不影响阅读，归档时按需复查）
- [x] 6.3 Drawio 图更新：`security-l3.drawio`（LLM/Tool 泳道改 chain phase 叙事 + stage 决策行含单调性说明，7 处）、`security-l2.drawio` / `agent-engine-l2.drawio`（"Guardrail Hooks (×4)" → "Guardrail Chains (4 phases)"）、`enterprise-foundation-l2.drawio`（"Guardrails (4 Hooks)" → "(4 chain phases)"）。核查结论：tool-platform-l2（hook 仅出现在非可见 id）、agent-studio-l2 / ecosystem-l2（Webhook 误报）无需更新。4 图 XML 校验通过
- [ ] 6.4 归档准备：feature catalog 中 1.3.5i (E3) / 1.3.4 / 9.4 增强标注与 roadmap 勾选；两条 Deferred Decisions 按 roadmap 延期表模式登记触发条件（路径 C 守卫链 ← 平台级配置面或路径收编；shell 感知下放规则层 ← 实际绕过案例）；ADR-030 接缝表落地标注（archive 流程时执行）
