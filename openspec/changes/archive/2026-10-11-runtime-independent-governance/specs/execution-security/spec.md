# Spec Delta

## MODIFIED Requirements

### Requirement: ToolWorker approval enforcement
ToolWorker SHALL check `ToolAccessPolicy.evaluate()` before executing any tool call. If the decision is `REQUIRE_APPROVAL`, the worker SHALL obtain approval through the execution-side approval binding (parameter digest, target resources, expiry, one-time consumption), and only execute the tool when the binding validation passes with `approved=True`. A bare callback decision without a valid binding (parameter mismatch, expired, revoked, or consumed by a different action) SHALL be treated as not approved. The same action recovering before actual dispatch MAY continue on its existing consumption association (re-checking current revocation, hard-deny, and expiry); completed actions replay their recorded ledger results without new approval.

#### Scenario: Tool requires approval — approved
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** the approval binding validates (matching parameter digest, unexpired, unconsumed) with `approved=True`
- **THEN** the tool is executed normally

#### Scenario: Tool requires approval — denied
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** the approval binding returns `approved=False`
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and rejection reason is returned

#### Scenario: Tool requires approval — timeout (fail-closed)
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** no approval callback is configured (None)
- **THEN** the tool is NOT executed (fail-closed)
- **AND** a tool result message with `is_error=True` and "no approval callback configured" is returned

#### Scenario: Tool requires approval — binding invalid despite callback approval
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call with parameters differing from the approved parameter digest, or the approval has expired / been revoked / been consumed by a different action
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and the binding failure reason is returned

#### Scenario: DENY decision blocks execution
- **WHEN** policy returns `DENY` for a tool call
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and deny reason is returned

### Requirement: ApprovalScope caching
When an approval is granted with `scope=SESSION`, the ApprovalCallback implementation SHALL cache the decision in-memory keyed by `(session_id, tool_name)` together with the approved parameter digest. Subsequent calls for the same tool in the same session SHALL return the cached decision without blocking only when the parameter digest matches; parameter changes, revocation, or policy hard-denial SHALL trigger re-judgment instead of a cache hit. ONCE-scope approvals remain non-cacheable.

#### Scenario: SESSION scope caches within session
- **WHEN** approval is granted with `scope=SESSION` for tool "terminal" with parameter digest D in session "s1"
- **AND** the same tool is called again in session "s1" with the same digest D
- **THEN** the cached approval is returned without a new blocking call

#### Scenario: SESSION cache does not bypass re-judgment on parameter change
- **WHEN** approval is granted with `scope=SESSION` for tool "terminal" with digest D
- **AND** the same tool is called in the same session with a different parameter digest
- **THEN** the cache is not used and a new approval request (re-judgment) is made

#### Scenario: ONCE scope does not cache
- **WHEN** approval is granted with `scope=ONCE` for tool "terminal"
- **AND** the same tool is called again
- **THEN** a new `request_approval()` call is made

## ADDED Requirements

### Requirement: 策略上下文携带经验证主体与预算字段

进入策略评估的上下文 SHALL 以纯数据形式携带经验证的身份信封要素(principal、`on_behalf_of`、`delegation_id`、数据域)与预算状态字段(余量耗尽/预留失败标记),使策略层可基于主体与预算做出拒绝;这些字段由服务端验证后的上下文派生,调用方自报值不进入策略评估。`runtime/tool_access.py` 继续保持零外部依赖,信封以标准库类型传入。

#### Scenario: 策略层可按主体拒绝

- **WHEN** 某工具规则按 principal 或委派范围配置了拒绝条件,且当前调用的身份信封命中该条件
- **THEN** 策略评估返回拒绝,拒绝原因可关联到身份信封要素

#### Scenario: 预算耗尽影响策略判定

- **WHEN** 上下文携带的预算字段表明该 principal/任务的计量余量已耗尽
- **THEN** 需要计量消耗的受保护动作在策略评估处被拒绝或按配置降级
