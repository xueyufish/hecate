# Spec Delta

## MODIFIED Requirements

### Requirement: A2A 执行的 agent 解析显式且按 workspace 界定

A2A 入口执行 SHALL 只解析显式配置范围内的目标 agent(配置声明的 workspace 作用域);作用域内的目标 agent 由该 workspace 的**默认部署**显式指定——部署表是显式登记面。作用域内存在恰好一个 agent 时沿用该 agent;存在多个 agent 时解析默认部署标记的 agent;多个 agent 且无默认部署(或默认部署不唯一)时,执行 MUST 以协议内失败状态拒绝。未配置作用域时同样拒绝。MUST NOT 回退到选取作用域之外的任何 agent(含按全局顺序取第一个);执行发生的任何归属(workspace、Task/Run 关联)取所解析 agent 行的 workspace。调用方身份→agent 的协议级映射仍是登记缺口(卡片级多 agent 服务面);在该缺口关闭前,默认部署解析是过渡契约。

#### Scenario: 未配置作用域时拒绝执行

- **WHEN** A2A 服务器未配置 agent 解析的 workspace 作用域而收到 SendMessage
- **THEN** 返回协议内失败状态的 Task(或等价 JSON-RPC 错误),不执行任何 agent,失败原因为配置缺失而非内部错误

#### Scenario: 作用域内单 agent 沿用

- **WHEN** 配置作用域的 workspace 内恰好存在一个未删除 agent
- **THEN** 该 agent 被解析执行,行为与既有语义一致

#### Scenario: 多 agent 按默认部署解析

- **WHEN** 配置作用域的 workspace 内存在多个 agent 且其中一个具有默认部署标记
- **THEN** 默认部署的 agent 被解析执行,归属取该 agent 的 workspace

#### Scenario: 多 agent 无默认部署拒绝

- **WHEN** 配置作用域的 workspace 内存在多个 agent 且无默认部署(或默认部署不唯一)
- **THEN** 执行以协议内失败状态拒绝,失败原因为解析不唯一而非内部错误

#### Scenario: 作用域外 agent 不可被选中

- **WHEN** 系统中存在配置作用域之外的 agent(包括其他 workspace 的 agent)
- **THEN** 这些 agent 不会被 A2A 执行路径选中,不存在按全局顺序回退选取

#### Scenario: 失败响应不泄露内部错误

- **WHEN** A2A 执行因内部异常失败并返回失败 Task
- **THEN** 面向协议客户端的消息为稳定的失败描述,不含异常堆栈、SQL/路径等内部细节;内部细节只进入服务端日志
