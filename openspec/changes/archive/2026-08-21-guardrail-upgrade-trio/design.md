# Design: guardrail-upgrade-trio

## Context

见 `proposal.md` 之 Why。当前代码事实（探索验证，作为设计输入）：

- **守卫栈未接线**：`ToolWorker` 在 `execution_service.py:587` 构造时不传 `access_policy`/`approval_callback`；`create_security_hooks` 全库零调用者；`ToolPolicyPipeline`/`policy_layers` 仅测试实例化。
- **路径结构**：chat 入口三分——路径 A（agent 带工具 → `_chat_with_tools` 直连循环，绕过 Pregel、无事件日志，生产主干）、路径 B（增强请求 → Pregel）、路径 C（纯透传 LLM 直调）。
- **1.3.19 基础设施就绪**：19 种事件类型、WAL 顺序、fold、投影等价断言、`LogInvariants` 注册表（未接线）、`CHANNEL_WRITE_REJECTED`（已定义未发射）。ADR-030 接缝契约定死三项升级的扩展方式。
- **已知缺陷**：`TOOL.PAIRING` 不变式读 `tool_calls`/`tool_call_id` key 但 ToolWorker 从未写过；`TOOL_CALL`/`TOOL_RESULT`/`LLM_RESPONSE` 缺 `log_schema_version`；SANITIZE 缺 `modified_data` 静默降级 ALLOW；`ModeLayer.override_decision` 提供 DENY→ALLOW 复活（死代码）。

约束：engine 层零外部依赖（stdlib only）；`EventType` 增量扩展（未知值读取降级 CUSTOM）；events 表零迁移；GitHub Flow + OpenSpec 流程。

## Goals / Non-Goals

**Goals:**

- 生产全路径（A + B）工具门控与守卫链强制生效，fail-closed
- 守卫从单槽 hook 升级为内核固定的有序瀑布链，全路径共用
- 审批具备持久审计对（事件日志）与 once-only 消费语义
- 危险模式检测升级为 shell 静态分析；拒绝单调不可复活
- 1.3.19 遗留缺陷（PAIRING key、版本标记、不变式接线）一并修复

**Non-Goals:**

- 路径 A 收编进 Pregel（引擎内子图建模，后续变更）
- `HookConfigModel`/session events 脚手架接活（另立变更）
- 审批 interrupt 化（挂起等人；本变更的 fail-closed 语义是"不等待、当场拒绝"，与挂起正交）
- 路径 C（纯透传）接 pre-LLM 守卫（见 Open Questions）
- shell 分析覆盖非 POSIX 方言（zsh 特性、fish 等）

## Decisions

### D1: 守卫链为 engine 层独立组件，执行操作是链的终点 stage

新模块 `engine/middleware.py`：链内核按 roadmap 的拦截点分相（phase）——`AGENT_PRE_STEP` / `AGENT_REQUEST`（LLM 请求前）/ `LLM_RESPONSE`（LLM 响应后）/ `TOOL_PRE_EXECUTE` / `TOOL_EXECUTE`（终点，执行真实操作）/ `TOOL_POST_EXECUTE` / `TOOL_RESULT`。每个 phase 持有序 stage 列表，stage 接口为 `next()` 委派（deer-flow ClarificationMiddleware 模式）：`async def handle(ctx, call_next) -> GuardResult`。真实执行（`llm_invoke` / `tool_execute`）作为终点 handler 传入链，守卫包裹执行而非在执行外游离调用——这是 E3 的架构核心，使 HITL、内容改写、门控都能以 stage 形态存在于同一链中。

链语义（顺序、BLOCK 短路、SANITIZE 传递、单调收紧）固化于内核循环，stage 无法重排或跳过其他 stage（ADR-029/030 约束："middleware stages are not pluggable chain mechanism"）。

**备选否决**：保留 4 个单槽 hook、在槽内做组合——无法表达跨拦截点的 stage（如 HITL 澄清），且两套 LLM 路径（LLMWorker / AgentExecutionPort）继续分叉。

### D2: 旧 hook ABC 经适配器入链，单 hook 构造参数保留兼容

`HookStageAdapter` 将 `PreLLMHook`/`PostLLMHook`/`PreToolHook`/`PostToolHook` 实现包装为单 stage（matcher 语义保留：不匹配即放行）。`LLMWorker`/`ToolWorker`/`AgentExecutionPort` 的单 hook 构造参数内部包装为单 stage 链，标记 deprecated 但不删除——现有测试与潜在外部使用者零破坏。`services/security/hooks/` 三个实现（Input/Output/ToolResult）不改写，经适配器直接入链。

### D3: HITL 走回调 + 事件持久化，不做 interrupt 化

fail-closed 的语义是"无应答者 → 当场拒绝"，不需要暂停执行。新组件 `services/security/approval.py`：

- 实现 `ApprovalCallback`，构造时注入 event_store 与会话上下文
- `request_approval` 前 append `APPROVAL_ASKED`，收到决定（或判定无应答者）后 append `APPROVAL_DECIDED`——无回调配置时同样产出完整审计对（decided=denied, reason="no answerer"）
- once-only 消费：已消费的 ONCE 授予按 `tool_call_id` 记忆，不可重放；SESSION+ 范围缓存仅当授予有持久 `APPROVAL_DECIDED` 记录时生效
- 维护 `ApprovalRecordModel` 投影（同一组件写事件与投影，事件为事实源，投影可重建）
- 会话恢复时从日志尾部重建会话级授予/拒绝集合（拒绝集支撑运行时单调拒绝，见 D6）

**备选否决**：迁移到 Pregel interrupt（挂起等人）——机制完整但无生产者，一步到位动 Pregel 风险大；且与本变更的"当场拒绝"语义正交，留给后续交互式审批场景。

### D4: 路径 A 与 Pregel 路径共用门控组件，事件直写日志

services 层新增装配门面（从 `agent.guardrail_config` + workspace 规则表构造链与策略）：

- 路径 B：`WorkflowExecutionService` 构造 `ToolWorker` 时传入 `access_policy`（规则从 `ToolPolicyModel`/`ToolPolicyRuleModel` 加载）、审批回调、守卫链
- 路径 A：`_chat_with_tools` 循环经同一门面获得链与策略，在每次 LLM 请求与工具执行处调用链
- 审批/回合事件由审批组件与执行入口直写 event_store（chat 路径已 Depends 注入 event_store）。`APPROVAL_*`/`TURN_*` 为 fold-skipped 类型，不参与通道重放，不会破坏投影等价断言
- `TURN_START`/`TURN_END`：Pregel 运行时在路径 B 回合首尾发射；路径 A 由循环入口/出口经同一 helper 发射

**效果差异边界**（写入文档）：MONOTONIC.DENIAL 日志不变式与回放校验覆盖全日志（两路径都写日志）；但投影等价断言、checkpoint 语义仍仅 Pregel 路径。

### D5: shell 静态分析 = 引号感知操作符分解 + shlex 分词 + 规范化匹配

新模块 `engine/shell_analysis.py`（stdlib only），被 `tool_access.py` 的危险模式匹配在工具为 shell 类时调用：

1. **分解**：引号感知地按 `|`、`&&`、`||`、`;`、换行切分为命令段
2. **分词**：每段 `shlex.split`（`posix=True`）
3. **规范化**：flag 簇排序（`-fr` → `-rf` 等价类），空白差异在分词后天然消除
4. **递归**：`$(...)`、反引号、`eval`/`sh -c`/`bash -c` 包装的内层内容递归分解检查（深度上限防爆炸）
5. **匹配**：每段的命令词 + 各 token 分别对照危险模式；解析失败时降级为对原始整串做现有 glob 匹配（降级为更保守的旧路径，**绝不降级为放行**）

**备选否决**：引入 `bashlex`——违反 engine 层零外部依赖；`tree-sitter-bash` 同理。shlex 子集覆盖常见绕过变体（双空格、flag 顺序、管道后段、命令替换），足够把 fnmatch 单参数匹配提升一个数量级。

### D6: 单调拒绝 = 运行时拒绝集 + 日志不变式双层

- **运行时层**：审批组件维护会话级拒绝集（key = `tool_call_id`）。`ToolWorker`/路径 A 循环在门控评估前查拒绝集：已拒绝的调用直接 DENY，不再重新评估、不再重新询问——堵死"下个 superstep 再问一次"的复活路径
- **日志层**：`MONOTONIC.DENIAL` 注册进 `LogInvariants`——日志中已有拒绝记录（`CHANNEL_WRITE_REJECTED` 或 `APPROVAL_DECIDED` denied）的调用若其后出现 `TOOL_CALL` 执行记录，报告违例。`loginvariants.run_all()` 接线到会话恢复路径（`_restore_from_checkpoint` 内 fold 之后执行，违例 fail-stop）
- **复活路径清除**：删除 `ModeLayer.override_decision`（无生产调用者，破坏面为零）；观察类需求未来经 sandbox 路由表达，不经 DENY 改写
- **身份地基**：`TOOL_CALL`/`TOOL_RESULT` payload 增补 `tool_call_id`（增量字段），配对、拒绝身份、审计归属都挂在其上；同步修 `TOOL.PAIRING` 的 key 错配

### D7: 决策审计补齐归属

`ToolWorker._check_access` 构造的决策上下文增补 `session_id`/`agent_id`/`workspace_id`/`on_behalf_of_user`（从 execution_context 透传），使 `ToolDecisionModel` 记录具备租户/会话归属。门控拒绝时发射 `CHANNEL_WRITE_REJECTED`（fold-skipped，audit-only）。

### D8: scope 过滤在装配期完成

per-agent stage 过滤不引入运行时过滤机制：装配门面按 `agent.guardrail_config` 决定构造哪些 stage（未启用的 hook 根本不进链）。满足规格"未启用的 stage 不参与"且避免链内每 stage 查配置的开销。SANITIZE 缺 `modified_data` 语义修复在链内核完成（违例 → BLOCK + stage 标识）。

## Risks / Trade-offs

- **[生产门控突然生效，既有 agent 工作流可能被阻断]** → 不提供门控总开关（规格要求强制生效）；上线摩擦经合规调优面管理：workspace 级 `ToolPolicyModel` ALLOW 规则、per-tool `risk_level`/`approval_required` 元数据调优。回滚 = 版本回滚。
- **[shlex 子集对复杂 shell 构造误判（漏判或误拦）]** → 漏判面不高于现状（旧 glob 匹配是子集）；误拦经 ALLOW 规则豁免；解析失败降级为旧整串 glob 匹配（保守方向）。
- **[路径 A 直写事件与 Pregel WAL 顺序交织]** → 审批/回合事件均为 fold-skipped 类型，不参与 fold 与等价断言；版本号由 event_store 单调分配，无回退风险。
- **[审批投影与事件日志瞬时不一致]** → 同一组件双写（先事件后投影），投影提供重建命令；审计以日志为准（log-as-truth）。
- **[REQUIRE_APPROVAL 路径每次多两次事件 append（延迟）]** → 仅审批路径发生，普通放行零额外数据库往返；审批本身即人机交互延迟量级，事件开销可忽略。
- **[旧 hook 单槽参数兼容层长期残留]** → deprecated 标记 + 文档指向链接口；清理挂到后续 cleanup（同 13.4a-6 软废弃先例）。

## Migration Plan

1. **零数据库迁移**：新事件类型与 `tool_call_id` 均在 payload 内；`approval_records` 表结构不变（语义变为投影）。
2. **部署顺序**：T0 接线随版本一次性生效（无开关，回滚靠版本回滚）；上线前建议在预发环境跑一遍高频 agent 工作流，用 workspace 规则预调优。
3. **存量数据**：存量事件缺 `log_schema_version`/`tool_call_id`——按 1.3.19 既有语义视为不可回放前缀，不回填；审批投影从部署后的新事件开始积累（存量无审批事件，投影起点为空是正确状态）。
4. **回滚**：回滚版本后，新事件类型在旧代码读取端降级为 CUSTOM（ADR-030 已验证的兼容行为），无 schema 锁死。

## Deferred Decisions（已定结论 + 触发条件）

- **路径 C（纯透传）不接 pre-LLM 守卫链**。理由不是延迟（注入检测为正则级，开销可忽略），而是配置面缺位：守卫链 scope 过滤挂 `agent.guardrail_config`，路径 C 请求可能无 agent 上下文，装配无配置来源；且该端点对标行业 `/v1/chat/completions` 裸 API 契约。触发条件：引入平台级守卫默认配置面时（或路径 A/C 收编落地时，孰先），路径 C 从平台面取 pre-LLM stages。
- **shell 危险模式规则集不开放工作区级自定义**。工作区自定义危险命令已可由现有 `ToolRule(DENY, arg_conditions)` 表达，且 DENY 层先于 ALLOW 层评估，语义上与危险模式的不可覆盖性几乎等价；唯一差距是 `arg_conditions` 仍为 glob 匹配（混淆变体可绕过软规则层，但内置硬模式仍拦得住等价命令）。V1 维持"内置规则 shell 感知 + 工作区规则 glob"。触发条件：出现工作区自定义规则被混淆绕过的实际案例，届时将 shell 感知匹配下放至 `arg_conditions` 层（规则表面向后兼容）。
