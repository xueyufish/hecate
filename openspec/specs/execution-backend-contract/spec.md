# execution-backend-contract Specification

## Purpose

定义"平台调用执行后端"方向的语言中立契约:权威 schema 与 Python 映射的关系、最小接口与能力三级声明、三轴归属与引用分离、事件 envelope 与游标、错误语义、执行制品 manifest 及版本策略,使内置 Runtime、自托管异构实现与供应商托管服务以统一可治理的方式接入。

## Requirements

### Requirement: 手写 schema 文件为权威契约源

契约层 MUST 以 `src/hecate/contracts/` 下手写的 JSON Schema 文件为权威接口定义(发布源);Python 类型 MUST 仅作内置映射(dataclass,不含 pydantic),MUST NOT 从 Python 类型导出或再生成 schema。每个契约主题 MUST 有标准样本(请求、事件、错误、能力声明),三方互检 MUST 由测试钉住:样本通过对应 schema 校验、Python 映射能解析全部样本、映射的往返序列化结果与样本语义一致。schema 文件、Python 映射、标准样本三者任一变更而其余未跟上时,MUST 使测试失败。

#### Scenario: 样本通过权威 schema 校验

- **WHEN** 测试以每个标准样本对相应 schema 文件执行校验
- **THEN** 全部样本通过,且样本覆盖每类请求、事件与错误

#### Scenario: Python 映射解析样本且往返一致

- **WHEN** 测试用 Python 映射解析标准样本并再次序列化
- **THEN** 字段语义与原样本一致,无字段静默丢失或改名

#### Scenario: 单侧变更被互检捕获

- **WHEN** 只修改 schema 文件而不更新映射或样本(或只改其中任一方)
- **THEN** 互检测试失败,指示漂移的契约主题

### Requirement: 契约层导入纯度

`src/hecate/contracts/` 与 `src/hecate/execution/` MUST NOT import SQLAlchemy、FastAPI、Pregel 具体类(runtime.pregel)或供应商 SDK——包括函数内、条件内与 try 块内的懒导入;违规 MUST 被 AST 全量扫描探针捕获并使测试失败(复用 runtime 自足探针的执法模式)。第三方实现者 MUST 能只依据 schema 文件与标准样本实现后端,不依赖 Hecate Python 对象、ORM 或平台进程。

#### Scenario: 懒导入逃逸被探针捕获

- **WHEN** 契约层内任一文件在函数体内新增 `from hecate.models...` 或 Pregel 具体类导入
- **THEN** 纯度探针测试失败

#### Scenario: 契约可独立阅读实现

- **WHEN** 实现者仅持有 schema 文件与标准样本
- **THEN** 可不安装 Hecate Python 包实现后端契约,请求/事件/错误语义无歧义

### Requirement: 最小接口与能力三级声明

`AgentExecutionBackend` MUST 只暴露最小六方法:`describe_capabilities`、`submit`、`get_run`、`read_events`、`list_artifacts`、`request_cancel`。可选能力(`provide_input`、`resolve_approval`、`pause`、`resume`、`export_context`)MUST 通过能力声明表达,每个能力取值 MUST 为 `unsupported` / `cooperative` / `enforced` 三级之一,并附验证来源与失效条件。对声明为 `unsupported` 的能力发起请求 MUST 返回结构化 UNSUPPORTED 错误,MUST NOT 返回伪造成功。能力面扩张 MUST 走能力声明协商,MUST NOT 通过新增接口方法表达。Graph、Channel、WorkerResult、checkpoint 与模型内部消息格式 MUST NOT 进入最低契约。

#### Scenario: 不支持暂停的后端拒绝暂停请求

- **WHEN** 对声明 `pause: unsupported` 的后端调用暂停
- **THEN** 收到结构化 UNSUPPORTED 错误,Run 状态不变,无伪造的暂停成功响应

#### Scenario: Stub 保留常驻负例

- **WHEN** 运行契约测试
- **THEN** StubExecutionBackend 永久声明 `pause: unsupported` 并断言上述负例行为

#### Scenario: 能力声明携带验证来源

- **WHEN** 读取某后端的能力声明
- **THEN** 每个非 unsupported 能力附验证来源(测试时间、版本或准入证据引用)与失效条件

### Requirement: 三轴归属与引用分离

能力模型 MUST 按 harness 所有方 × 环境所有方 × 工具与数据访问执行点三轴登记,不得以单一托管标签推断整体数据边界。供应商 session/turn 引用与平台 Task/Run 引用 MUST 在契约类型上分离,MUST NOT 相互混用或以一方冒充另一方。执行请求中的标识 MUST 为带签发域的逻辑引用,接收方 MUST NOT 被要求查询平台 ORM 解析;独立宿主从可信本地登记分配标识,受管 adapter 负责两侧映射。供应商专属配置 MUST 放命名空间字段,核心只验证通用字段,MUST NOT 从命名空间读取平台管理员权限。

#### Scenario: 托管后端声明 session 引用而非 Task/Run

- **WHEN** 托管后端返回运行标识
- **THEN** 返回的是带签发域的 session/turn 引用,平台 Task/Run 引用与之后端会话引用类型分离、不可互换

#### Scenario: 无 ORM 解析的请求处理

- **WHEN** 后端接收携带逻辑引用的 ExecutionRequest
- **THEN** 全部字段可由请求本身与本地登记解析,不要求访问平台数据库

#### Scenario: 供应商命名空间不提升权限

- **WHEN** 供应商命名空间配置包含声称管理权限的字段
- **THEN** 核心校验不赋予其任何平台权限,命名空间仅由对应 adapter 的 schema 验证

### Requirement: 事件 envelope 与游标读取

跨进程事件 MUST 使用统一 envelope:event_id、task/run 引用、source_sequence、correlation/causation 引用、事件与接收时间、payload schema 引用与证据引用。`read_events` MUST 以不透明游标分页,断线后 MUST 能用游标恢复续读,MUST NOT 要求接收方重建全局顺序;事件缺口 MUST 显式标记。后端原始内部事件(Pregel superstep 等)MUST 保留为后端详情,MUST NOT 要求其他后端复制。

#### Scenario: 游标断线续读

- **WHEN** 消费方持游标在事件流中断后重新读取
- **THEN** 从游标位置续读,已消费事件不重复投递,缺口被显式标记而非静默跳过

#### Scenario: envelope 字段完整

- **WHEN** 检查任一跨进程事件样本
- **THEN** envelope 携带全部必填字段,payload 以 schema 引用声明其结构

### Requirement: 错误语义六类且超时不等于失败

错误 MUST 表达为六类:`unsupported`、`authorization_denied`、`budget_exhausted`、`version_conflict`、`unreachable`、`outcome_unknown`。超时或连接中断 MUST 映射为 `outcome_unknown`(待对账),MUST NOT 自动等同任务失败。`version_conflict` 覆盖两种情形:契约版本超出支持窗口;相同幂等键携带不同请求内容重复提交(此时 MUST 在 detail 中标记 `idempotency_key_content_mismatch`)。两种情形的冲突响应均 MUST 指向原请求/支持窗口。错误响应 MUST 携带可关联的请求引用与机器可读错误码,MUST NOT 只有自由文本。

#### Scenario: 超时表现为结果未知

- **WHEN** 提交后连接超时且无法确认后端是否开始执行
- **THEN** 错误为 `outcome_unknown` 并附对账指引,Run 不被标记为 failed

#### Scenario: 幂等键冲突

- **WHEN** 相同幂等键携带不同请求内容重复提交
- **THEN** 返回冲突响应并指向原请求,不静默覆盖

### Requirement: 执行制品 manifest

执行制品 MUST 以标准归档(tar.gz)加摘要清单表达:manifest 声明 schema/后端类型与兼容版本、定义入口、逐文件 sha256 摘要、所需能力、工具 schema 与权限声明、模型/可选组件配置引用、产物 schema 及批准/评测证据引用。MUST NOT 自创专用包格式,MUST NOT 自动执行清单中的安装脚本;后端专属图/脚本 MUST 放命名空间(其他 Runtime 无须实现 Pregel DSL);manifest MUST NOT 包含明文凭据、业务数据或在线平台 ID 查找前提。摘要不匹配或清单结构非法的制品 MUST 在加载前被拒绝。

#### Scenario: 摘要不匹配被拒绝

- **WHEN** 归档内任一文件内容与 manifest 声明的 sha256 不符
- **THEN** 制品校验失败并指出冲突文件,不进入加载

#### Scenario: 专属图留在命名空间

- **WHEN** 检查制品 manifest 中 Pregel 图定义的位置
- **THEN** 位于后端命名空间字段内,通用字段不要求任何后端理解 Pregel DSL

### Requirement: 草案版本与按能力独立版本化

本契约 MUST 标记 0.x 草案、未冻结;首个可发布版本的冻结以 step8 真实异构后端验证为前置,MUST NOT 仅凭 Stub 通过测试冻结接口。执行契约与 Sandbox 契约(及未来 Memory/Evaluation 契约)MUST 各自独立携带版本号,MUST NOT 存在全平台同步版本号。破坏性变更 MUST 通过新契约版本引入并附迁移说明;不兼容版本组合 MUST 以 `version_conflict` 拒绝。

#### Scenario: 版本字段独立存在

- **WHEN** 读取执行契约与 Sandbox 契约的版本标识
- **THEN** 两者版本各自独立,可分别演进

#### Scenario: 不兼容版本被拒绝

- **WHEN** 请求声明使用的契约版本超出接收方支持窗口
- **THEN** 返回 `version_conflict` 错误并说明支持范围,不降级猜测语义

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
