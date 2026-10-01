# execution-backend-contract Delta

## ADDED Requirements

### Requirement: HTTP/JSON binding with problem+json error mapping

契约 MUST 提供权威的 OpenAPI 3.1 绑定文件作为首个进程外传输绑定,`$ref` 复用既有权威 schema 而非复制定义;绑定 MUST 覆盖能力发现、提交、状态查询、事件游标读取、控制命令(取消)与 artifact 列举,流式订阅(SSE)MUST 声明为可选视图——同一事件序列的订阅视角,断线后客户端 MUST 能用游标查询恢复,流式 MUST NOT 成为唯一读取方式。错误响应 MUST 采用 RFC 9457 problem+json 载体,`type` 指向契约错误 URI;后端可返回的错误码与 HTTP 状态映射 MUST 固定为:`unsupported`→501、`authorization_denied`→403、`budget_exhausted`→429(可附 `Retry-After`)、`version_conflict`→409;`unreachable` 与 `outcome_unknown` MUST NOT 作为后端错误响应出现——`unreachable` 是调用方在传输失败时自行记录的状态,`outcome_unknown` 是 run 状态 payload 中的状态值。绑定文档 MUST 声明传输中立:其他传输由 adapter 映射同一语义,不要求所有后端暴露 HTTP。

#### Scenario: HTTP samples validate against the OpenAPI binding

- **WHEN** 校验器检查 HTTP 层标准样本(请求/响应对)与 OpenAPI 文件
- **THEN** 全部样本通过 OpenAPI media schema 校验,且 schema 定义经 `openapi-spec-validator` 结构校验合法

#### Scenario: Returnable errors arrive as problem+json

- **WHEN** 后端拒绝请求并返回六码中的可返回错误(如 `authorization_denied`)
- **THEN** HTTP 响应为映射状态码(403)+ problem+json body,`type` 指向契约错误 URI,错误字段与 `errors.schema.json` 一致

#### Scenario: Caller-synthesized states are never backend error responses

- **WHEN** 提交或查询发生传输超时/连接失败,或后端自身无法判定远端动作结果
- **THEN** 调用方在本地记录 `unreachable`/`outcome_unknown` 并按对账策略处理;后端的 HTTP 错误响应与 run 状态 payload 中不得把二者作为 problem+json 错误码返回,`outcome_unknown` 仅作为状态值出现

#### Scenario: Stream disconnect falls back to cursor read

- **WHEN** SSE 流式视图断开
- **THEN** 客户端用最后收到的游标经普通事件读取端点恢复,事件序列与游标语义不因传输视图不同而变化

### Requirement: Out-of-process identity and authorization transfer

契约 MUST 定义进程外调用的身份与授权声明集 schema:声明名称与约束(`iss`、`aud`=目标后端服务身份、`sub`=调用方工作负载身份、租户作用域、委派链引用、短时效),样本 MUST 携带声明集合而非真实令牌(密码学验证归实现 adapter)。OpenAPI MUST 提供 bearer 与 mutualTLS 两个 securityScheme profile。后端→平台的回调 MUST 单独定义受众绑定的回调凭据要求。授权上下文 MUST 沿用既有 `authorization` 引用类型传递凭据引用而非内联密钥。调用方 body 或请求参数中自报的身份/角色 MUST 被接收方忽略——身份只来自经校验的传输层声明。

#### Scenario: Claim set carries required fields

- **WHEN** 校验安全声明样本
- **THEN** 每个声明集包含 `iss`/`aud`/`sub`/租户作用域,缺失 `aud`(受众缺失可致令牌跨服务重放)的样本校验失败

#### Scenario: Self-asserted role in body is ignored

- **WHEN** 请求 body 携带 `role: admin` 而传输层声明中无对应授权
- **THEN** 接收方按声明集判定,body 自报字段不产生任何权限提升(负例样本钉住)

#### Scenario: Callback carries audience-bound credential

- **WHEN** 后端向平台发起回调(如托管审批决议)
- **THEN** 回调携带绑定平台回调受众的独立凭据,不得复用入站调用方令牌

### Requirement: Tool declaration contract

契约 MUST 定义跨后端的工具声明 schema:输入/输出 JSON Schema 引用、工具版本与副作用类别;副作用类别枚举 MUST 与内部 `SideEffectClass` 五值一致(`readonly`/`idempotent_write`/`non_idempotent_write`/`external_side_effect`/`unknown`),不另造词汇。四类失败的分层 MUST 固定:参数验证失败在提交/分发前按 schema 拒绝;业务拒绝是工具的**结果**而非执行错误(Run 继续,否定结果随工具结果事件传递);系统故障表达为工具级错误事件;远端结果未知复用 `outcome_unknown` 语义并按幂等 ID 进入对账,不盲目重试。模型对工具的候选/实际选择 MUST 可作为可观察事实关联到同一 Run(供诊断),不要求暴露隐藏推理。

#### Scenario: Side-effect vocabulary matches the internal enum

- **WHEN** 校验工具声明样本的副作用类别
- **THEN** 取值落在五值枚举内,与 `runtime/tool_side_effects.py` 的 `SideEffectClass` 一致,无同义新造词

#### Scenario: Business rejection is a tool result, not an execution error

- **WHEN** 工具执行成功返回业务否定结果(如目标库存不足)
- **THEN** 结果作为工具结果事件携带否定 payload,Run 不因此进入错误状态

#### Scenario: Parameter validation fails before dispatch

- **WHEN** 工具参数不符合声明 schema
- **THEN** 调用在分发到业务目标前被拒绝,拒绝记录可查询,不产生副作用

#### Scenario: Remote outcome unknown reconciles by idempotent id

- **WHEN** 远端工具调用响应丢失或超时
- **THEN** 状态记为 `outcome_unknown` 并按幂等 ID 对账,不重试产生第二次副作用

### Requirement: Hosted backend mapping semantics

契约 MUST 提供托管后端映射语义文档:平台 task 与供应商 session、run 与 turn 的映射关系,供应商持有其会话状态、平台仅保存必要引用与投影;`reconciliation.strategy` MUST 增加 `query_by_vendor_session` 策略——提交响应丢失时,支持该能力的后端按供应商会话标识对账,无法对账时 MUST 标记未知而非盲目重建会话。供应商内部 subagent 事件 MUST 映射为命名空间化的 Run 内部事件,MUST NOT 自动获得平台 Team 成员身份或独立委派权限。

#### Scenario: Lost submit response reconciles by vendor session

- **WHEN** 提交请求已到达托管后端但响应丢失,且后端声明支持按会话对账
- **THEN** 平台按供应商会话标识查询对账并恢复提交结果;后端不支持该策略或无法对账时,提交状态标记为未知

#### Scenario: Subagent event stays run-internal

- **WHEN** 托管后端上报其内部 subagent 的执行事件
- **THEN** 事件以命名空间化细节事件归属对应 Run,不生成任何平台侧 Team 成员记录或委派授权
