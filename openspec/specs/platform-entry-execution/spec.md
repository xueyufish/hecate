# platform-entry-execution Specification

## Purpose

平台入口执行应用服务:被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行)经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),执行装配(含事件存储与 checkpoint)发生在入口服务内,平台 adapter 与独立宿主共享同一装配。同步与流式接口是同一次执行上的等待/订阅视图,不引入独立执行生命周期。

## Requirements

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

### Requirement: 入口执行关联统一 Task/Run

业务入口(HTTP/MCP/IM/A2A)与定时任务 agent 入口经入口服务的每次执行 SHALL 在 TaskRunRegistry 登记 Task(发起者身份链、workspace 归属)与 Run(尝试、身份链快照),会话型执行并登记 conversation 链接;同一 agent 的同一请求经不同入口执行时,可按 agent/会话追溯到关联的 Task/Run。A2A 与定时任务执行的 workspace 归属 SHALL 取执行所用 agent 行的 workspace,不得无 workspace 归属。评估等平台内部批量入口 SHALL 将其执行关联到单一内部 Task,而非逐条目新建 Task。登记 MUST NOT 改变执行行为;登记失败时执行结果中显式标记关联缺失,MUST NOT 静默宣称已登记。Task 生命周期状态机(queued/waiting_approval 等)属 step6,本能力 MUST NOT 引入其语义。

#### Scenario: 跨入口追溯同一 Task/Run 关联

- **WHEN** 同一 agent 分别经 HTTP 与 MCP 入口执行并查询其会话的关联记录
- **THEN** 两次执行均可解析到登记的 Task 与 Run,身份链与 workspace 归属可查

#### Scenario: A2A 与定时任务执行关联 Task

- **WHEN** A2A SendMessage 或定时 agent 触发经入口服务完成执行
- **THEN** 该执行按所选 agent 的 workspace/agent 归属登记 Task/Run,可按 agent 追溯

#### Scenario: 评估执行关联单一内部 Task

- **WHEN** 一次评估运行触发多条 workflow 执行
- **THEN** 这些执行关联到该评估的单个内部 Task,不逐条目创建独立 Task

#### Scenario: 登记失败显式可见

- **WHEN** Task/Run 登记未能完成而执行继续
- **THEN** 执行结果或日志显式标记关联缺失,不以已登记状态上报

### Requirement: 引擎事件映射到平台契约

入口服务 SHALL 将引擎原始流事件映射为 `contracts/execution` 的 `EventEnvelope`(task_ref/run_ref、source_sequence、payload schema 引用),并支持按 run 引用与游标的分页读取;原始引擎事件 MUST 作为后端详情保留,不因映射而丢弃。tool call/result 的配对在映射层校验,未配对事件 MUST 显式暴露(校验失败或告警),不得静默吞掉。客户端可见的 OpenAI 风格响应与 SSE chunk 由适配层转换产生,MUST NOT 携带 backend 专属字段(引擎内部路由/状态通道名等)。

#### Scenario: 事件可按 run 与游标读取

- **WHEN** 一次执行完成后按 run 引用读取事件页并携带上次游标
- **THEN** 返回的 envelope 序列连续、可翻页,且包含 tool 事件的配对语义

#### Scenario: 原始事件作为后端详情保留

- **WHEN** 读取映射后的事件
- **THEN** 每个可映射事件的后端详情中可取得原始引擎事件

#### Scenario: 客户端 chunk 不含 backend 专属字段

- **WHEN** 检查流式响应的 SSE chunk 字段集合
- **THEN** 字段限于 OpenAI chunk 语义,引擎内部状态字段不出现在客户端可见输出中

### Requirement: 入口层禁止新增引擎具体导入

被迁移的入口层模块(channel/api、tools/mcp、channel/im、channel/a2a/server)MUST NOT 直接 import 引擎具体实现类(`PregelRuntime`、`GraphCompiler`);执行装配只发生在入口服务与共享装配内。该约束 SHALL 由分层测试自动化执行,违规时测试失败并指出模块与符号。

#### Scenario: 分层测试拦截具体导入

- **WHEN** 入口层模块(含 channel/a2a/server)新增对 `PregelRuntime` 或 `GraphCompiler` 的 import
- **THEN** 分层测试失败并指出违规模块与符号

### Requirement: 执行包与宿主的升级支持窗口显式

hecate-runtime(内置执行包)、hecate-runner(宿主)与平台主体的组合升级支持窗口 SHALL 以显式契约/依赖矩阵成文(兼容组合、边界版本、退出条件),并被 CI 断言与 lock/workspace 实际版本一致;矩阵外组合 MUST NOT 被静默当作受支持组合。

#### Scenario: 矩阵与实际版本一致

- **WHEN** CI 校验契约/依赖矩阵
- **THEN** 矩阵声明的组合与 workspace/lock 中的实际版本边界一致

#### Scenario: 矩阵外组合不被宣称支持

- **WHEN** 组合落在矩阵声明的边界之外
- **THEN** 文档与矩阵不将其列为受支持组合,文档中的支持窗口与矩阵一一对应

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

### Requirement: 入口关联原子性与真实主体

入口服务 MUST 按 agent/workspace 解析真实 AgentPrincipal,MUST NOT 把 Agent ID 当作 Principal ID。关联失败 MUST 使用 savepoint 回滚本次关联,不污染调用方已有事务,不返回内部 SQL/错误详情;实际执行 session 与关联 session MUST 一致。内部 workflow 评估 MUST 使用真实 runtime factory,传递 workspace、固定 workflow 版本、共享存储和 guardrail,并在持久 trajectory 中保留关联结果。

#### Scenario: 真实 Principal 与 Agent ID 不同

- **WHEN** 使用正常注册的 Agent 执行,Principal ID 与 Agent ID 不同
- **THEN** Run 的身份快照指向真实 Principal,关联成功

#### Scenario: Run 登记 flush 失败

- **WHEN** Task 创建后 Run 的 flush 失败
- **THEN** 本次关联回滚且返回安全 missing 原因,已有事务仍可使用,不保留孤立 Task

### Requirement: 共享装配保留引擎提交日志与工具作用域

共享执行装配 MUST 把注入的事件存储同时用于引擎和 Worker,生成 TURN、CHANNEL_WRITE 与 STEP_END,不得只写工具事件并声称有完整提交日志。共享工具装配 MUST 按 workspace 加载和执行工具;其他 workspace 的同名工具 MUST NOT 参与当前 Agent 的执行。

#### Scenario: 通过共享装配执行一次完整 Run

- **WHEN** 使用配置事件存储的 builtin 或平台执行路径完成一次执行
- **THEN** 同一 session 可读取 TURN_START/TURN_END、CHANNEL_WRITE 和 STEP_END
