# Spec Delta — unified-chat-execution

## Purpose

将聊天入口收敛到共享执行应用服务和统一的 Task/Run、动作、审批及事件语义。开放任务目标为不依赖 Pregel 的 Agent Controller，明确流程由 Workflow Executor 承接；Controller 尚待交付。现行 Pregel chat 图与直连循环保持迁移兼容契约，切流与默认启用仍需逐入口证据，活跃会话保留原绑定。

## MODIFIED Requirements

### Requirement: 有工具 agent 经统一运行时执行

配置了工具的 agent 的 chat 请求(流式与非流式)的路由 SHALL 按解析后的生效开关决定:workspace 级覆盖(如设置)优先于全局 `CHAT_TOOL_LOOP_ENGINE_ENABLED`,两者均缺省时为关闭。生效开关开启时执行 SHALL 经平台入口执行服务与现行引擎路径(Pregel chat 图)完成,产生引擎事件(含 `TOOL_CALL`/`TOOL_RESULT` 回执);开关关闭时 SHALL 保持直连循环行为不变。本要求规定的是迁移期开关路由契约:Pregel chat 图与直连循环均为兼容路径,收敛目标以"收敛目标为统一执行应用服务及语义"要求为准。会话 SHALL 保持路径亲缘:`session_resume`/会话续接沿用该会话已建立的路径,一次进行中的流式请求不受并发开关变更影响。引擎 ToolWorker 的 guardrail 装配 SHALL 与直连路径等价(`middleware_chains`、`denial_tracker`、`event_store` 完整下传)。带知识库或增强功能的请求 SHALL NOT 丢失 agent 配置的工具。workspace 覆盖的每次变更 SHALL 写入放量记录(workspace、操作者、旧值→新值、时间);兼容期直连循环不具备引擎路径的恢复/回放保证,G2 关闭前不得对新路径宣称可靠副作用恢复。

#### Scenario: 开关开启时有工具 agent 走引擎

- **WHEN** 生效开关为开启且配置了工具的 agent 发起 chat 请求
- **THEN** 执行经 Pregel 图完成,事件日志含配对的 `TOOL_CALL`/`TOOL_RESULT`

#### Scenario: 开关关闭时行为不变

- **WHEN** 生效开关为关闭(默认)
- **THEN** 有工具 agent 的 chat 请求走直连循环,行为与迁移前一致

#### Scenario: 关态行为有真实入口钉住

- **WHEN** 全局开关关闭且无 workspace 覆盖,经真实 HTTP 入口发起有工具 agent 的流式与非流式请求
- **THEN** 请求走直连循环且响应语义与迁移前一致;引擎事件日志不因该请求产生条目;直连循环不经引擎事件日志、无持久回放保证的限制保持显式文档化,不因引擎路径成为推荐路径而被默写抹去

#### Scenario: workspace 覆盖优先于全局开关

- **WHEN** 全局开关为 `false` 而某 workspace 覆盖为开启
- **THEN** 该 workspace 的请求走引擎路径,其他 workspace 不受影响

#### Scenario: 会话续接保持路径亲缘

- **WHEN** 某会话既有轮次经直连循环完成,生效开关随后变更
- **THEN** 该会话的续接/恢复仍沿用直连路径,不因开关变更中途切换

#### Scenario: 覆盖变更写入放量记录

- **WHEN** 修改某 workspace 的覆盖值
- **THEN** 放量记录写入 workspace、操作者、旧值→新值与时间,可审计查询

#### Scenario: 增强分支不再丢失工具

- **WHEN** 带工具的 agent 携带 `kb_ids` 发起请求(生效开关开启)
- **THEN** agent 配置的工具在执行中可用(不再仅客户端工具)

## ADDED Requirements

### Requirement: 收敛目标为统一执行应用服务及语义

聊天入口执行 SHALL 以统一执行应用服务及统一的 Task/Run、动作、审批与事件语义为收敛目标,不以编译 Pregel 图作为自主执行的必经路径。开放任务的目标执行模型是 Runtime Agent Controller:以显式持久状态推进模型决策、受控动作、观察、等待与完成,不要求构造 Pregel 图;明确控制规则的任务由 Workflow Executor 承接,Pregel 承担图执行。Agent Controller 能力在交付并通过所属验收前 SHALL 登记为待实现,平台文档、规范与支持矩阵不得宣称已支持,也不得据该目标宣称入口收敛已完成。默认启用翻转 SHALL 继续要求逐入口 G3 证据与显式决策记录;新任务按路由切换,活跃 Run/会话保持原实现与状态格式,不以新配置重解释旧 checkpoint。共享执行应用服务对 Pregel 图执行的唯一类型绑定是待解除的实现现状,不是目标契约。

#### Scenario: Controller 未交付前不宣称收敛完成

- **WHEN** 共享执行应用服务仍直接绑定 Pregel 图执行且 Agent Controller 未交付
- **THEN** 平台文档、规范与支持矩阵将聊天收敛登记为未完成,不宣称"已统一到目标执行模型"

#### Scenario: 收敛目标变更不翻转默认开关

- **WHEN** 本规范的收敛目标变更被接受
- **THEN** 生效开关缺省值保持关闭,默认启用仍需逐入口 G3 证据与显式决策记录

#### Scenario: 活跃会话保持原路径

- **WHEN** 收敛目标变更后,某会话既有轮次经直连循环或 Pregel chat 图完成
- **THEN** 该会话续接沿用原路径与状态格式,不因目标变更中途切换或重解释 checkpoint
