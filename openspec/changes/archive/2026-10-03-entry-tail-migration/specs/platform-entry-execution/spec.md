# platform-entry-execution Delta

## MODIFIED Requirements

### Requirement: 平台入口统一消费一个执行应用服务

被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行、A2A executor、定时任务 agent executor)SHALL 经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),MUST NOT 内联构造执行服务或自带装配。入口服务经平台 adapter 消费共享装配,装配(含事件存储与 checkpoint)在入口间一致;同步与流式接口是同一次执行上的等待/订阅视图,MUST NOT 引入独立执行生命周期。已迁移入口模块 MUST NOT 保留绕过执行服务的直连模型调用;仍走非统一装配的路径(如定时任务 workflow 经 studio 测试入口)MUST 以显式登记的绕过路径记录,不得与已迁移入口混淆。

#### Scenario: MCP 入口装配与 HTTP 一致

- **WHEN** 同一配置工具的 agent 分别经 HTTP chat 与 MCP `agent_chat` 执行
- **THEN** 两者的事件日志均含配对的 `TOOL_CALL`/`TOOL_RESULT`,MCP 执行不再缺少事件与 commit point 产出

#### Scenario: 入口模块不得内联装配执行服务

- **WHEN** 检查被迁移入口模块的 import
- **THEN** 不存在对执行服务的直接构造调用,执行仅经入口服务发起

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

### Requirement: 入口层禁止新增引擎具体导入

被迁移的入口层模块(channel/api、tools/mcp、channel/im、channel/a2a/server)MUST NOT 直接 import 引擎具体实现类(`PregelRuntime`、`GraphCompiler`);执行装配只发生在入口服务与共享装配内。该约束 SHALL 由分层测试自动化执行,违规时测试失败并指出模块与符号。

#### Scenario: 分层测试拦截具体导入

- **WHEN** 入口层模块(含 channel/a2a/server)新增对 `PregelRuntime` 或 `GraphCompiler` 的 import
- **THEN** 分层测试失败并指出违规模块与符号

### Requirement: 每次入口迁移附真实入口回归

每一组入口链的迁移 SHALL 附带经真实入口(不 mock 执行服务本身)的回归:流式与非流式、多轮工具调用、审批拒绝路径,以及该入口特有的失败模式;迁移前该入口的可观察行为(响应 schema、SSE 格式、finish_reason、审批记录)在迁移后保持兼容。部分入口未迁移时只记录该切片完成,MUST NOT 勾选整个入口迁移项。

#### Scenario: 真实入口回归覆盖双模式

- **WHEN** 运行被迁移入口的回归套件
- **THEN** 流式与非流式请求均经真实协议入口执行并通过,响应格式与迁移前基准一致

#### Scenario: A2A 协议响应保持兼容

- **WHEN** A2A SendMessage 经迁移后的入口执行并返回 Task
- **THEN** Task/Artifact 字段集合与状态枚举与迁移前基准一致,A2A 客户端无需变更

#### Scenario: 切片完成不被夸大

- **WHEN** 定时任务 workflow 入口仍经 studio 测试入口执行
- **THEN** 记录只声明已完成切片,入口迁移整体项保持未勾选
