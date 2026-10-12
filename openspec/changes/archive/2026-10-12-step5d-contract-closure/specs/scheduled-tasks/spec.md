# Spec Delta

## ADDED Requirements

### Requirement: 定时 Workflow 执行经平台入口路径

定时任务触发 workflow 执行时,执行 SHALL 经平台入口执行服务完成(与定时 agent 执行同构):登记 Task/Run 关联与身份链、装配治理与事件、产生引擎事件与平台信封;`ScheduledTaskExecution.result_summary` 的结果绑定语义保持不变。测试导向的 workflow 运行器 SHALL 收缩为 studio 测试端点专用,定时路径 MUST NOT 再经其执行。

#### Scenario: 定时 workflow 关联 Task/Run

- **WHEN** 定时任务触发一次 workflow 执行并完成
- **THEN** 该执行登记 Task/Run 关联(按任务创建者 workspace 归属),result_summary 记录输出摘要,引擎事件经入口路径产生

#### Scenario: 定时路径不再经测试运行器

- **WHEN** 检查定时 workflow 执行的代码路径
- **THEN** 其执行经入口执行服务,测试运行器仅被 studio 测试端点使用
