## ADDED Requirements

### Requirement: 入口关联原子性与真实主体

入口服务 MUST 按 agent/workspace 解析真实 AgentPrincipal，MUST NOT 把 Agent ID 当作 Principal ID。关联失败 MUST 使用 savepoint 回滚本次关联，不污染调用方已有事务，不返回内部 SQL/错误详情；实际执行 session 与关联 session MUST 一致。内部 workflow 评估 MUST 使用真实 runtime factory，传递 workspace、固定 workflow 版本、共享存储和 guardrail，并在持久 trajectory 中保留关联结果。

#### Scenario: 真实 Principal 与 Agent ID 不同

- **WHEN** 使用正常注册的 Agent 执行，Principal ID 与 Agent ID 不同
- **THEN** Run 的身份快照指向真实 Principal，关联成功

#### Scenario: Run 登记 flush 失败

- **WHEN** Task 创建后 Run 的 flush 失败
- **THEN** 本次关联回滚且返回安全 missing 原因，已有事务仍可使用，不保留孤立 Task

### Requirement: 共享装配保留引擎提交日志与工具作用域

共享执行装配 MUST 把注入的事件存储同时用于引擎和 Worker，生成 TURN、CHANNEL_WRITE 与 STEP_END，不得只写工具事件并声称有完整提交日志。共享工具装配 MUST 按 workspace 加载和执行工具；其他 workspace 的同名工具 MUST NOT 参与当前 Agent 的执行。

#### Scenario: 通过共享装配执行一次完整 Run

- **WHEN** 使用配置事件存储的 builtin 或平台执行路径完成一次执行
- **THEN** 同一 session 可读取 TURN_START/TURN_END、CHANNEL_WRITE 和 STEP_END
