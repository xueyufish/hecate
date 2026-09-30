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
