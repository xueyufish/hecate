# a2a-protocol Delta

## ADDED Requirements

### Requirement: A2A 执行的 agent 解析显式且按 workspace 界定

A2A 入口执行 SHALL 只解析显式配置范围内的目标 agent(配置声明的 workspace 作用域);解析结果 MUST 恰为一个该作用域内的 agent。未配置作用域、或作用域内无法唯一解析时,执行 MUST 以协议内失败状态拒绝,MUST NOT 回退到选取作用域之外的任何 agent(含按全局顺序取第一个);执行发生的任何归属(workspace、Task/Run 关联)取所解析 agent 行的 workspace。完整 per-caller/per-agent 身份仍是登记缺口,退出条件为显式的调用方→agent 映射;在该缺口关闭前,本要求的拒绝语义是过渡契约。

#### Scenario: 未配置作用域时拒绝执行

- **WHEN** A2A 服务器未配置 agent 解析的 workspace 作用域而收到 SendMessage
- **THEN** 返回协议内失败状态的 Task(或等价 JSON-RPC 错误),不执行任何 agent,失败原因为配置缺失而非内部错误

#### Scenario: 作用域外 agent 不可被选中

- **WHEN** 系统中存在配置作用域之外的 agent(包括其他 workspace 的 agent)
- **THEN** 这些 agent 不会被 A2A 执行路径选中,不存在按全局顺序回退选取

#### Scenario: 失败响应不泄露内部错误

- **WHEN** A2A 执行因内部异常失败并返回失败 Task
- **THEN** 面向协议客户端的消息为稳定的失败描述,不含异常堆栈、SQL/路径等内部细节;内部细节只进入服务端日志
