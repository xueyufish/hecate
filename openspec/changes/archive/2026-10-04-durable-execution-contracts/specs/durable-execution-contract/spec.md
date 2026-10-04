# Spec Delta

## Purpose

定义持久执行的语言中立契约与最小接缝:Task 生命周期状态机、控制命令回执、幂等提交键、治理事件 envelope 的 actor/source profile、持久 Action 台账四态与游标恢复语义。本契约是 step6 两个并行交付(`durable-execution-core` 的持久化实现、`platform-task-control-api` 的平台接入)的共同基线:双方只消费本契约,不并行定义同义状态或接口。

## ADDED Requirements

### Requirement: Task 生命周期状态为封闭枚举并与后端观察态分列

契约 SHALL 定义 Task 生命周期状态 `queued / running / waiting_input / waiting_approval / succeeded / failed / cancelled / reconciliation_required` 为封闭枚举;新增状态 MUST 经契约版本演进,未知状态值 MUST 被拒绝而不是静默映射。`succeeded / failed / cancelled` 为终态,终态 MUST NOT 回退到非终态;`reconciliation_required` MUST NOT 被静默改写为成功或失败,只能经对账显式收敛。Task 生命周期状态与 `contracts/execution` 既有的 `RunState`(后端观察态)为不同维度,MUST NOT 互相覆盖写入;一方推导另一方时 MUST 作为显式投影并保留来源。契约层 MUST NOT 规定状态由谁写入——字段所有权(平台 Task 投影、宿主本地权威)由消费方契约声明,本契约只固定状态语义与迁移约束。

#### Scenario: 未知状态值被拒绝

- **WHEN** 反序列化遇到枚举外的 Task 生命周期状态字符串
- **THEN** 解析失败并返回显式错误,不产生默认值或静默映射

#### Scenario: 终态不回退

- **WHEN** 已处于 `succeeded` 的 Task 收到迁移到 `running` 的写入
- **THEN** 契约校验拒绝该迁移,Task 保持终态

#### Scenario: 待对账不被静默收敛

- **WHEN** Task 处于 `reconciliation_required` 且未完成对账
- **THEN** 任何实现 MUST NOT 将其报告为 `succeeded` 或 `failed`,只能保持待对账或经对账动作显式收敛

### Requirement: 控制命令为独立回执记录

每个控制命令 SHALL 产生独立记录,携带 `command_id`、命令种类(至少涵盖取消、暂停、恢复、补充输入)、签发者、目标 task/run 引用与签发时间;记录状态 SHALL 为 `requested / acknowledged / applied / rejected / expired` 封闭集合,其中 `expired` 表示命令超过有效期未被应用。同一命令 MUST 只有单一权威记录方;收到协议层成功响应(如 HTTP 200)MUST NOT 被解释为 `applied`——`applied` 只能由执行方实际生效的回执产生。命令可携带期望的 task/run revision 以拒绝过期页面发起的命令;不支持相应命令种类的实现 MUST 返回显式拒绝,MUST NOT 返回伪造成功。

#### Scenario: 协议成功不等于已应用

- **WHEN** 控制命令提交返回传输层成功但执行方尚未回执
- **THEN** 命令记录状态为 `requested` 或 `acknowledged`,不显示为 `applied`

#### Scenario: 过期命令进入 expired

- **WHEN** 命令携带的有效期届满时仍未被应用
- **THEN** 记录状态迁移为 `expired`,且 MUST NOT 再被应用

#### Scenario: 过期 revision 被拒绝

- **WHEN** 命令携带的期望 revision 与当前记录 revision 不一致
- **THEN** 命令被拒绝,拒绝记录可追溯期望值与实际值

### Requirement: 幂等提交键绑定主体、工作区与请求摘要

幂等提交键 SHALL 绑定服务端验证的调用主体、workspace 与请求体摘要;键作用域 MUST 来自服务端身份上下文,MUST NOT 采用请求内自报的主体或 workspace。相同键、相同摘要的重复提交 SHALL 返回同一 Task/Run 关联,不产生第二次执行;相同键、不同摘要的提交 SHALL 返回冲突错误并指明已登记的摘要类别,不覆盖原有关联。摘要 MUST 使用规范化序列化后计算,使语义相同的请求体产生相同摘要。

#### Scenario: 同键同体幂等

- **WHEN** 相同主体以相同幂等键与相同请求摘要重复提交
- **THEN** 返回与首次提交相同的 Task/Run 关联,不创建新执行

#### Scenario: 同键异体冲突

- **WHEN** 相同主体以相同幂等键提交不同请求摘要
- **THEN** 返回冲突错误,原有关联不被改写

### Requirement: 治理事件复用 envelope 并要求 actor 与 source

治理事件 SHALL 复用 `EventEnvelope` 结构;envelope SHALL 新增可选 `actor` 与 `source` 字段,且二者在治理事件 profile 下 MUST 必填——缺失任一字段的治理事件 MUST 被拒绝。`source` SHALL 标识事件的权威写入方(平台、宿主、执行后端),使平台直接观察的动作与远程自报事件可区分来源等级。事件读取 SHALL 复用游标语义:断线后以游标续读,缺口以 gap 标记显式呈现,事件顺序以 `source_sequence` 为准,MUST NOT 以客户端接收时间或时钟重建顺序。关键治理事件 MUST 可完整留存与导出,MUST NOT 依赖采样。

#### Scenario: 缺 actor 的治理事件被拒绝

- **WHEN** 构造缺少 `actor` 字段的治理事件
- **THEN** 校验失败,该事件不进入事件流

#### Scenario: 游标续读不重建顺序

- **WHEN** 消费方断线后携带最后游标续读事件流
- **THEN** 返回游标之后连续的事件,缺口以 gap 标记显式呈现,顺序由 `source_sequence` 决定

### Requirement: 持久 Action 台账四态与安全恢复

Action 台账契约 SHALL 定义四态 `never_started / claimed / outcome_unknown / store_unavailable`:意图(动作键、工具名或动作名、参数摘要、副作用类别)MUST 先于业务分发持久化;领取 MUST 原子,并发领取者至多一个成功;恢复 MUST 区分四态——`never_started` 按新调用执行,`claimed` 下仅明确幂等类别可重放,`outcome_unknown` 不自动重放并标记待对账,`store_unavailable` 下写操作安全停止且 MUST NOT 被解释为从未执行。已成功的动作恢复时 SHALL 返回真实结果引用或结果摘要;结果不可得时 SHALL 返回显式待对账标记,MUST NOT 以占位文本冒充结果。同一动作键的参数摘要变化 SHALL 返回显式冲突并拒绝执行。本契约的状态与副作用类别取值 MUST 与 `tool-recovery` 的 Runtime 层语义保持一致,一致性 SHALL 由测试钉住,任何一侧变更而另一侧未跟上时测试失败。

#### Scenario: 意图先于分发

- **WHEN** 动作在持久化意图前发生分发路径故障
- **THEN** 台账中不存在该动作的领取记录,恢复判定为 `never_started`,可安全按新调用执行

#### Scenario: 并发领取至多一个

- **WHEN** 两个执行者同时尝试领取同一动作键
- **THEN** 至多一个领取成功,另一个收到已被领取的显式结果

#### Scenario: 参数摘要冲突拒绝

- **WHEN** 恢复时同一动作键携带的参数摘要与已记录摘要不一致
- **THEN** 返回冲突错误,不执行该调用,不区分副作用类别

#### Scenario: 未知结果不冒充成功

- **WHEN** 动作回执状态为 `outcome_unknown` 或结果不可得
- **THEN** 恢复返回待对账标记(附已记录摘要),不返回占位结果,不自动重放

### Requirement: 接缝最小化并有第二实现与共同验收集

持久执行接缝 SHALL 仅包含最小方法集:`DurableTaskStore`(Task 状态记录与迁移校验、幂等键关联)、`ControlCommandRecorder`(命令记录与状态迁移)、`ActionLedger`(意图、领取、回执、恢复查询)。接缝 MUST NOT 泄漏存储细节(ORM 对象、SQL、连接句柄),出入参 MUST 为契约 dataclass;接口语义由权威 schema 与标准样本承载,第三方仅依据 schema 与样本即可实现。每种接缝 MUST 附带 InMemory Stub 第二实现与参数化契约测试;后续每个生产实现(PostgreSQL、平台 adapter、宿主 adapter)MUST 通过同一契约测试集才可接入。契约模块 MUST 保持既有纯度约束:不 import SQLAlchemy、FastAPI、Pregel 具体类或供应商 SDK(含懒导入),违规由 AST 探针捕获。

#### Scenario: 契约测试对实现参数化

- **WHEN** 新的生产实现(如 PostgreSQL 存储)注册进契约测试参数集
- **THEN** 同一组语义断言(状态迁移、幂等、四态恢复、命令回执)对该实现全部通过,方可接入

#### Scenario: 接缝不泄漏存储细节

- **WHEN** 检查接缝方法的签名与返回类型
- **THEN** 全部为契约 dataclass 与内建类型,不出现 ORM 模型、连接或 SQL 对象

#### Scenario: 纯度探针覆盖新契约模块

- **WHEN** 契约或接缝模块引入 SQLAlchemy、FastAPI、Pregel 具体类或供应商 SDK 的 import(含函数内懒加载)
- **THEN** AST 纯度探针测试失败并指明模块与符号
