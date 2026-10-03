# platform-entry-execution Delta

## MODIFIED Requirements

### Requirement: 平台入口统一消费一个执行应用服务

被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行、A2A executor、定时任务 agent executor)SHALL 经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),MUST NOT 内联构造执行服务或自带装配。入口服务经平台 adapter 消费共享装配;装配一致性以实例为单位界定:单进程内引擎事件存储 SHALL 只有一个共享实例(由应用生命周期装配注册,入口只读消费),入口模块 MUST NOT 自建存储单例、MUST NOT 依赖与其他入口语义耦合的私有装配函数——工具注册/加载等入口侧共享装配 SHALL 来自唯一的公开装配来源。同步与流式接口是同一次执行上的等待/订阅视图,MUST NOT 引入独立执行生命周期。已迁移入口模块 MUST NOT 保留绕过执行服务的直连模型调用;仍走非统一装配的路径(如定时任务 workflow 经 studio 测试入口)MUST 以显式登记的绕过路径记录,不得与已迁移入口混淆。

#### Scenario: MCP 入口装配与 HTTP 一致

- **WHEN** 同一配置工具的 agent 分别经 HTTP chat 与 MCP `agent_chat` 执行
- **THEN** 两者的事件日志均含配对的 `TOOL_CALL`/`TOOL_RESULT`,MCP 执行不再缺少事件与 commit point 产出

#### Scenario: 跨入口共享同一事件存储实例

- **WHEN** 一次执行经任一已迁移入口(如 MCP)写入引擎事件后,从进程内另一入口使用的存储读取同一会话/run 的事件
- **THEN** 事件可读且内容一致;不存在按入口分裂的多个存储实例(进程内后端下尤其不得互相不可见)

#### Scenario: 入口模块不得内联装配执行服务

- **WHEN** 检查被迁移入口模块的 import
- **THEN** 不存在对执行服务的直接构造调用、自建的事件存储单例、或对其他入口模块私有装配函数的导入,执行与装配仅经入口服务和公开装配来源发起

#### Scenario: 尾链迁移后无直连模型绕过

- **WHEN** 检查 A2A executor 与定时任务 agent executor 的执行路径
- **THEN** 不存在绕过入口服务的 `llm_service` 直连调用,两条链与 HTTP 入口共享同一装配语义(工具面、guardrail、事件/checkpoint)

#### Scenario: 未迁移链显式登记

- **WHEN** 查看仍经 studio 测试入口执行的定时任务 workflow 路径
- **THEN** 该路径绕过入口服务的现状有登记记录,不与已迁移入口混淆

### Requirement: 每次入口迁移附真实入口回归

每一组入口链的迁移 SHALL 附带经真实入口(不 mock 执行服务本身)的回归:流式与非流式、多轮工具调用、审批拒绝路径、会话中断后恢复(断线/恢复)路径,以及该入口特有的失败模式;迁移前该入口的可观察行为(响应 schema、SSE 格式、finish_reason、审批记录)在迁移后保持兼容。部分入口未迁移时只记录该切片完成,MUST NOT 勾选整个入口迁移项。

#### Scenario: 真实入口回归覆盖双模式

- **WHEN** 运行被迁移入口的回归套件
- **THEN** 流式与非流式请求均经真实协议入口执行并通过,响应格式与迁移前基准一致

#### Scenario: 会话中断后恢复有真实入口证据

- **WHEN** 同一会话的执行在入口断开后以同一会话标识再次经真实入口发起
- **THEN** 恢复执行的测试证明状态/事件连续性与 tool call/result 配对,不因入口切换或断线静默丢失

#### Scenario: A2A 协议响应保持兼容

- **WHEN** A2A SendMessage 经迁移后的入口执行并返回 Task
- **THEN** Task/Artifact 字段集合与状态枚举与迁移前基准一致,A2A 客户端无需变更

#### Scenario: 切片完成不被夸大

- **WHEN** 定时任务 workflow 入口仍经 studio 测试入口执行
- **THEN** 记录只声明已完成切片,入口迁移整体项保持未勾选
