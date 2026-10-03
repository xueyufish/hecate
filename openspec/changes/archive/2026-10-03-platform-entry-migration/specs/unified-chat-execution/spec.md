# unified-chat-execution Delta

## MODIFIED Requirements

### Requirement: 有工具 agent 经统一运行时执行

配置了工具的 agent 的 chat 请求(流式与非流式)的路由 SHALL 按解析后的生效开关决定:workspace 级覆盖(如设置)优先于全局 `CHAT_TOOL_LOOP_ENGINE_ENABLED`,两者均缺省时为关闭。生效开关开启时执行 SHALL 经平台入口执行服务与 Pregel chat 图完成,产生引擎事件(含 `TOOL_CALL`/`TOOL_RESULT` 回执);开关关闭时 SHALL 保持直连循环行为不变。会话 SHALL 保持路径亲缘:`session_resume`/会话续接沿用该会话已建立的路径,一次进行中的流式请求不受并发开关变更影响。引擎 ToolWorker 的 guardrail 装配 SHALL 与直连路径等价(`middleware_chains`、`denial_tracker`、`event_store` 完整下传)。带知识库或增强功能的请求 SHALL NOT 丢失 agent 配置的工具。workspace 覆盖的每次变更 SHALL 写入放量记录(workspace、操作者、旧值→新值、时间);兼容期直连循环不具备引擎路径的恢复/回放保证,G2 关闭前不得对新路径宣称可靠副作用恢复。

#### Scenario: 开关开启时有工具 agent 走引擎

- **WHEN** 生效开关为开启且配置了工具的 agent 发起 chat 请求
- **THEN** 执行经 Pregel 图完成,事件日志含配对的 `TOOL_CALL`/`TOOL_RESULT`

#### Scenario: 开关关闭时行为不变

- **WHEN** 生效开关为关闭(默认)
- **THEN** 有工具 agent 的 chat 请求走直连循环,行为与迁移前一致

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

### Requirement: 引擎路径具备真实入口证据

引擎路径的正确性 SHALL 以真实入口测试证明,不以内联替换执行服务的 mock 分支测试替代:覆盖流式与非流式、多轮工具调用、审批拒绝、断线/恢复与取消,并核对 tool call/result 配对与审批拒绝记录的传递。真实入口证据按入口逐条积累;某入口缺证据时,该入口不得视为已收敛,默认开关保持关闭。

#### Scenario: 真实 HTTP 入口多轮流式

- **WHEN** 经真实 HTTP/SSE 入口发起需要两轮工具调用的流式 chat
- **THEN** 最终答案按既有 SSE 语义下发,事件日志含两轮配对的 `TOOL_CALL`/`TOOL_RESULT`,中间迭代不下发内容块

#### Scenario: 审批拒绝经真实入口传递

- **WHEN** 经真实入口触发需审批工具且审批被拒
- **THEN** 拒绝记录产生并传递到该入口的响应语义,与直连路径等价

#### Scenario: 断线后恢复

- **WHEN** 流式请求中断后经会话续接恢复
- **THEN** 恢复沿用该会话既有的路径与状态,不产生重复工具副作用

#### Scenario: 入口缺证据不切默认

- **WHEN** 某入口尚无真实入口证据
- **THEN** 该入口不宣称收敛,全局默认开关不因此翻转
