# Spec Delta

## MODIFIED Requirements

### Requirement: 平台入口统一消费一个执行应用服务

被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行、A2A executor、定时任务 agent executor)SHALL 经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),MUST NOT 内联构造执行服务或自带装配。入口服务经平台 adapter 消费共享装配;装配一致性以实例为单位界定:单进程内引擎事件存储 SHALL 只有一个共享实例(由应用生命周期装配注册,入口只读消费),入口模块 MUST NOT 自建存储单例、MUST NOT 依赖与其他入口语义耦合的私有装配函数——工具注册/加载等入口侧共享装配 SHALL 来自唯一的公开装配来源。同步与流式接口是同一次执行上的等待/订阅视图,MUST NOT 引入独立执行生命周期。已迁移入口模块 MUST NOT 保留绕过执行服务的直连模型调用;仍走非统一装配的路径(如定时任务 workflow 经 studio 测试入口)MUST 以显式登记的绕过路径记录,不得与已迁移入口混淆。定时任务调度器触发 SHALL 经 executor registry 将执行分派到任务目标对应的执行器并如实记录执行结果;不执行实际工作、直接记为成功的空转路径 MUST NOT 存在。

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

#### Scenario: 调度触发真实分派

- **WHEN** 定时任务调度器因 cron 触发执行一个已启用的任务
- **THEN** 执行经 executor registry 分派到该任务目标(agent/workflow)对应的执行器,执行记录反映执行器的真实结果而非无条件成功

### Requirement: 引擎事件映射到平台契约

入口服务 SHALL 将引擎原始流事件映射为 `contracts/execution` 的 `EventEnvelope`(task_ref/run_ref、source_sequence、payload schema 引用),并支持按 run 引用与游标的分页读取;原始引擎事件 MUST 作为后端详情保留,不因映射而丢弃。tool call/result 的配对在映射层校验,未配对事件 MUST 显式暴露(校验失败或告警),不得静默吞掉。客户端可见的 OpenAI 风格响应与 SSE chunk 由适配层转换产生,MUST NOT 携带 backend 专属字段(引擎内部路由/状态通道名等)。经平台任务控制面派发的执行,其映射后的事件 SHALL 持久追加到平台事件存储,分页读取从持久存储出发:跨请求与进程内重连后按游标恢复,不依赖进程内内存保留;内存保留仅可用于未经任务控制面派发的执行。

#### Scenario: 事件可按 run 与游标读取

- **WHEN** 一次执行完成后按 run 引用读取事件页并携带上次游标
- **THEN** 返回的 envelope 序列连续、可翻页,且包含 tool 事件的配对语义

#### Scenario: 原始事件作为后端详情保留

- **WHEN** 读取映射后的事件
- **THEN** 每个可映射事件的后端详情中可取得原始引擎事件

#### Scenario: 客户端 chunk 不含 backend 专属字段

- **WHEN** 检查流式响应的 SSE chunk 字段集合
- **THEN** 字段限于 OpenAI chunk 语义,引擎内部状态字段不出现在客户端可见输出中

#### Scenario: 任务控制面事件持久可恢复

- **WHEN** 经任务控制面派发的执行产生流事件后,以新的读取请求(非原请求上下文)按 run 引用从游标 0 读取
- **THEN** 事件从持久存储返回,与执行时映射的 envelope 一致,不因原请求结束而丢失
