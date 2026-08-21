## MODIFIED Requirements

### Requirement: ToolWorker approval enforcement
ToolWorker SHALL check `ToolAccessPolicy.evaluate()` before executing any tool call. If the decision is `REQUIRE_APPROVAL`, the worker SHALL emit an `APPROVAL_ASKED` 事件、调用 `ApprovalCallback.request_approval()`、在收到决定后 emit `APPROVAL_DECIDED` 事件，且仅当决定为 `approved=True` 时执行工具。两个审批事件 SHALL 被同一回合（TURN）封闭。当决策为 `REQUIRE_APPROVAL` 但无已配置的应答者（回调未配置或返回无决定）时，系统 SHALL 拒绝该调用并仍产出完整的审批审计对（asked + decided=denied）。

#### Scenario: Tool requires approval — approved
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** `ApprovalCallback.request_approval()` returns `ApprovalDecision(approved=True)`
- **THEN** the tool is executed normally
- **AND** `APPROVAL_ASKED` 与 `APPROVAL_DECIDED` 事件均已写入事件日志，且二者处于同一 TURN 封闭区间

#### Scenario: Tool requires approval — denied
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** `ApprovalCallback.request_approval()` returns `ApprovalDecision(approved=False)`
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and rejection reason is returned
- **AND** `APPROVAL_DECIDED` 事件记录 denied 决定

#### Scenario: Tool requires approval — 无应答者 fail-closed
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** no approval callback is configured (None)
- **THEN** the tool is NOT executed (fail-closed)
- **AND** 事件日志中仍可查到 `APPROVAL_ASKED` 与随后的 `APPROVAL_DECIDED`（denied，原因标明无应答者）

#### Scenario: DENY decision blocks execution
- **WHEN** policy returns `DENY` for a tool call
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and deny reason is returned

### Requirement: ApprovalScope caching
When an approval is granted, the system SHALL only honor scope-based caching when the grant is backed by a durable `APPROVAL_DECIDED` event. `ONCE` scope grants SHALL be consumed on first use: the same approval SHALL NOT authorize more than one tool execution. `SESSION` scope caching SHALL key by `(session_id, tool_name)` and require a durable decided record; in-memory-only grants SHALL NOT be treated as cacheable.

#### Scenario: ONCE scope 消费后不可重放
- **WHEN** approval is granted with `scope=ONCE` for tool "terminal"
- **AND** the approved tool executes once
- **THEN** a subsequent call for the same tool SHALL trigger a new approval request, not reuse the consumed grant

#### Scenario: SESSION scope caches within session
- **WHEN** approval is granted with `scope=SESSION` for tool "terminal" in session "s1"
- **AND** the grant is recorded as a durable `APPROVAL_DECIDED` event
- **AND** the same tool is called again in session "s1"
- **THEN** the cached approval is returned without a new blocking call

#### Scenario: 无持久记录的授予不可缓存
- **WHEN** an approval callback returns `approved=True` with `scope=SESSION`
- **AND** no durable `APPROVAL_DECIDED` record exists for the grant
- **THEN** the grant is treated as ONCE and consumed on first use

### Requirement: ApprovalRecord model
The system SHALL define `ApprovalRecordModel(BaseModel)` in `models/approval.py` with fields: `workspace_id` (UUID), `tool_name` (String 255), `session_id` (UUID, nullable), `user_id` (UUID, nullable), `scope` (String 20, default "once"), `status` (String 20, default "pending"), `risk_level` (String 20), `reason` (Text, nullable), `expires_at` (DateTime, nullable). 该模型 SHALL 作为事件日志中审批事件对的可重建投影（服务审批队列查询），而非独立的事实源：投影内容 SHALL 可从 `APPROVAL_ASKED` / `APPROVAL_DECIDED` 事件重建，运行时不得绕过事件日志直接以该表作为授权判断的权威来源。

#### Scenario: Create approval record
- **WHEN** an `APPROVAL_ASKED` event is appended
- **THEN** the projection contains a matching record with `status="pending"`

#### Scenario: Status values
- **WHEN** the paired `APPROVAL_DECIDED` event records approval or rejection
- **THEN** the projected record's `status` becomes `"approved"` or `"rejected"` correspondingly

#### Scenario: 投影可重建
- **WHEN** 投影表被清空后从事件日志重放审批事件
- **THEN** 投影内容与清空前一致

## ADDED Requirements

### Requirement: 单调拒绝不变式
工具调用一经拒绝（policy DENY、审批拒绝、守卫链 BLOCK 任一来源），该次调用的拒绝 SHALL 不可被后续任何组件复活为放行：任何组件 SHALL NOT 将既有的 DENY 决定改写为 ALLOW。检测到拒绝被复活的执行 SHALL fail-stop 并报告 `MONOTONIC.DENIAL` 不变式违例。

#### Scenario: 拒绝后同调用不可被重新评估为放行
- **WHEN** 同一工具调用标识已被拒绝
- **AND** 后续组件对该调用返回放行类决定
- **THEN** 该调用仍不执行，且系统报告 `MONOTONIC.DENIAL` 不变式违例

#### Scenario: AUDIT 模式不可复活拒绝
- **WHEN** 审计/观察类运行模式遇到 DENY 决定
- **THEN** 该决定保持 DENY；系统不提供 DENY→ALLOW 的改写路径

#### Scenario: 拒绝事件入日志
- **WHEN** 工具调用因任何拒绝来源未执行
- **THEN** 事件日志中存在对应的拒绝记录（含拒绝来源与原因）

### Requirement: 生产路径门控强制生效
工具门控（访问策略评估与审批）SHALL 在生产执行路径上强制生效：Pregel 路径的工具节点执行与直连工具循环（chat 路径 A）的工具执行均 MUST 经过门控，不存在绕过门控的生产工具执行路径。

#### Scenario: Pregel 路径工具被门控
- **WHEN** Pregel 路径执行一个 `risk_level="critical"` 且无审批的工具调用
- **THEN** 该调用被拒绝执行（fail-closed）

#### Scenario: 直连循环工具被门控
- **WHEN** chat 路径 A 的直连工具循环执行同一调用
- **THEN** 该调用同样被拒绝执行，与 Pregel 路径行为一致

#### Scenario: 门控决策携带归属信息
- **WHEN** 任一生产路径产出工具门控决策审计记录
- **THEN** 记录包含 workspace/session/agent 归属标识
