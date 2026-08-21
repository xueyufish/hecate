## MODIFIED Requirements

### Requirement: EventType enum defines standard event categories
The engine SHALL define a string enum `EventType` with values: `NODE_START`, `NODE_END`, `TOOL_CALL`, `TOOL_RESULT`, `CHANNEL_WRITE`, `CHANNEL_WRITE_REJECTED`, `LLM_REQUEST`, `LLM_RESPONSE`, `INTERRUPT`, `RESUME`, `ERROR`, `PII_DETECTED`, `CUSTOM`, `STEP_END`, `EVICTION`, `SUBGRAPH_START`, `SUBGRAPH_END`, `ORCHESTRATOR_DECISION`, `ORCHESTRATOR_EVALUATION`, `APPROVAL_ASKED`, `APPROVAL_DECIDED`, `TURN_START`, `TURN_END`.

#### Scenario: Use standard event type
- **WHEN** `EventType.TOOL_CALL` is referenced
- **THEN** it SHALL equal the string `"TOOL_CALL"`

#### Scenario: Custom event type
- **WHEN** an event is created with `event_type=EventType.CUSTOM` and `payload={"custom_type": "my_event"}`
- **THEN** the event SHALL be valid and storeable

#### Scenario: New boundary and semantic event types
- **WHEN** `EventType.STEP_END`, `EventType.EVICTION`, `EventType.SUBGRAPH_START`, `EventType.SUBGRAPH_END`, or `EventType.CHANNEL_WRITE_REJECTED` is referenced
- **THEN** each SHALL equal its string value and be storeable via the existing append contract

#### Scenario: 审批与回合事件类型
- **WHEN** `EventType.APPROVAL_ASKED`, `EventType.APPROVAL_DECIDED`, `EventType.TURN_START`, or `EventType.TURN_END` is referenced
- **THEN** each SHALL equal its string value and be storeable via the existing append contract

#### Scenario: Unknown historical event types fall back on read
- **WHEN** `_row_to_event` reads an event_type string not present in the enum
- **THEN** it SHALL fall back to `EventType.CUSTOM`（既有行为保持）

### Requirement: 事件 payload 携带 log_schema_version

本 change 起新写入的事件 payload SHALL 携带 `log_schema_version` 字段（值为 `2`）。缺失该字段的存量事件 SHALL 被消费方视为不可回放前缀。events 表结构 SHALL 零迁移（版本标记仅存于 payload）。

会话语义层事件（`TOOL_CALL`、`TOOL_RESULT`、`LLM_RESPONSE`）SHALL 与通道层事件一样携带 `log_schema_version`；任何新增事件发射点 SHALL 一并携带该标记。

#### Scenario: 新事件带版本标记
- **WHEN** 引擎 append 任意新事件
- **THEN** payload SHALL 含 `log_schema_version: 2`

#### Scenario: 存量事件无标记
- **WHEN** 读取历史事件（payload 无 `log_schema_version`）
- **THEN** 消费方 SHALL 将其判定为不可回放前缀

#### Scenario: 会话语义层事件补齐标记
- **WHEN** 工具节点或 LLM worker 发射 `TOOL_CALL` / `TOOL_RESULT` / `LLM_RESPONSE` 事件
- **THEN** payload 含 `log_schema_version: 2`，fold 不会将其判为不可回放前缀

## ADDED Requirements

### Requirement: 回合边界事件
Pregel 运行时 SHALL 在每次用户回合开始时发射 `TURN_START` 事件、回合结束时发射 `TURN_END` 事件，payload 携带 `log_schema_version`。回合边界事件作为审批事件对的封闭区间与回合级分析的锚点。

#### Scenario: 回合事件成对出现
- **WHEN** 一次会话执行完成一个用户回合
- **THEN** 事件日志中存在配对的 `TURN_START` 与 `TURN_END`，且后者版本号大于前者

#### Scenario: 审批对处于回合封闭区间
- **WHEN** 事件日志中存在 `APPROVAL_ASKED` 与其后的 `APPROVAL_DECIDED`
- **THEN** 二者均位于同一对 `TURN_START` / `TURN_END` 之间

#### Scenario: 跨回合审批对判定违例
- **WHEN** `APPROVAL_DECIDED` 与其 `APPROVAL_ASKED` 不在同一 TURN 封闭区间内
- **THEN** 日志不变式检查报告审批对封闭违例

### Requirement: CHANNEL_WRITE_REJECTED 发射
被守卫拒绝的通道写入（工具调用被门控或守卫链拒绝后本应产生的写入）SHALL 以 `CHANNEL_WRITE_REJECTED` 事件记录（audit-only，fold 跳过），payload 携带拒绝来源与原因及 `log_schema_version`。

#### Scenario: 拒绝写入留痕但不入状态
- **WHEN** 工具调用被门控拒绝
- **THEN** 事件日志中存在 `CHANNEL_WRITE_REJECTED` 记录
- **AND** fold 重放时跳过该事件，不产生通道状态变更

### Requirement: 日志不变式注册表接入执行
`LogInvariants` 注册表 SHALL 在会话恢复/回放校验路径上执行（`run_all` 接线），且 `TOOL.PAIRING` 不变式读取的 payload key 与工具 worker 实际发射的 payload 结构一致。新增 `MONOTONIC.DENIAL` 不变式：已被拒绝的工具调用不得在其后的日志中出现对应执行记录，检测到即报告违例。

#### Scenario: TOOL.PAIRING 使用真实 payload key
- **WHEN** 工具 worker 发射 `TOOL_CALL`（payload 含 `tool_name`）后日志跨 `STEP_END` 仍无对应 `TOOL_RESULT`
- **THEN** `TOOL.PAIRING` 不变式报告违例（key 与实际发射结构一致，检查真实生效）

#### Scenario: MONOTONIC.DENIAL 检测拒绝后执行
- **WHEN** 日志中某工具调用已有拒绝记录（`CHANNEL_WRITE_REJECTED` 或 `APPROVAL_DECIDED` denied）
- **AND** 其后出现同一调用的 `TOOL_CALL` 执行记录
- **THEN** `MONOTONIC.DENIAL` 不变式报告违例

#### Scenario: 正常日志零违例
- **WHEN** 对合法执行产生的事件日志运行全部注册不变式
- **THEN** 无违例报告
