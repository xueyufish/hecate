# durable-execution-contract Specification

## Purpose

定义持久执行的语言中立契约与最小接缝:Task 生命周期状态机、控制命令回执、幂等提交键、治理事件 envelope 的 actor/source profile、持久 Action 台账四态与游标恢复语义。本契约是 step6 两个并行交付(`durable-execution-core` 的持久化实现、`platform-task-control-api` 的平台接入)的共同基线:双方只消费本契约,不并行定义同义状态或接口。

## Requirements

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

durable-core 既有接缝 SHALL 仅包含最小方法集:`DurableTaskStore`(Task 状态记录与迁移校验、幂等键关联)、`ControlCommandRecorder`(命令记录与状态迁移)、`ActionLedger`(意图、领取、回执、恢复查询)。接缝 MUST NOT 泄漏存储细节(ORM 对象、SQL、连接句柄),出入参 MUST 为契约 dataclass;接口语义由权威 schema 与标准样本承载,第三方仅依据 schema 与样本即可实现。每种接缝 MUST 附带 InMemory Stub 第二实现与参数化契约测试;后续每个生产实现(PostgreSQL、平台 adapter、宿主 adapter)MUST 通过同一契约测试集才可接入。契约模块 MUST 保持既有纯度约束:不 import SQLAlchemy、FastAPI、Pregel 具体类或供应商 SDK(含懒导入),违规由 AST 探针捕获。

Runtime 单步决策另消费 Runtime-owned `AcceptedDecisionStore`（lookup/commit）;宿主从可选 runtime bridge 注入 SQL 实现,不把该 ABC 加入 core `seams.py` 或使 core 反向依赖 Runtime。此内部接缝同样 SHALL 不泄漏 ORM/连接,并使用版本化记录和共同契约测试;它不新增外部执行协议或宣称 Python 类是语言无关发布产物。

#### Scenario: 契约测试对实现参数化

- **WHEN** 新的生产实现(如 PostgreSQL 存储)注册进契约测试参数集
- **THEN** 同一组语义断言(状态迁移、幂等、四态恢复、命令回执)对该实现全部通过,方可接入

#### Scenario: 接缝不泄漏存储细节

- **WHEN** 检查接缝方法的签名与返回类型
- **THEN** 全部为契约 dataclass 与内建类型,不出现 ORM 模型、连接或 SQL 对象

#### Scenario: 纯度探针覆盖新契约模块

- **WHEN** 契约或接缝模块引入 SQLAlchemy、FastAPI、Pregel 具体类或供应商 SDK 的 import(含函数内懒加载)
- **THEN** AST 纯度探针测试失败并指明模块与符号

### Requirement: 已接受 Agent 决策的持久化语义

持久层 SHALL 为已接受 Agent 决策提供原子提交:决策记录(输出、动作集合、版本引用)与其动作集合在单事务内落盘,提交完全成功或完全不可见。同一部署作用域内 (Run 引用, 决策步骤标识) SHALL 唯一;重复提交内容相同返回原记录(幂等),内容不同返回显式冲突且不覆盖原决策。提交 SHALL 携带 ownership/fencing 上下文并复用既有租约/fencing 机制——过期持有者的提交 SHALL 被拒绝,至多接受一个版本。存储不可用 SHALL 显式失败,不得返回"提交成功"。实现 SHALL 有 SQL 与 InMemory 两个可实例化实现并注册进同一参数化契约套件;决策记录为独立事实,动作结果仍由既有 Action 台账管理。

#### Scenario: 原子提交后重启找回原决策

- **WHEN** 决策与动作集合提交成功后,新进程实例以相同 (Run 引用, 决策步骤标识) 查找
- **THEN** 返回原输出、动作实际参数与动作键,与提交内容逐字一致

#### Scenario: 相同内容重复提交返回原记录

- **WHEN** 同一 (Run 引用, 决策步骤标识) 以完全相同的内容再次提交
- **THEN** 返回原记录,不产生第二条记录,模型无关的调用方无冲突错误

#### Scenario: 内容不同显式冲突不覆盖

- **WHEN** 同一 (Run 引用, 决策步骤标识) 以不同输出或不同动作集合提交
- **THEN** 返回显式冲突错误并携带原决策标识,原决策记录不变

#### Scenario: 过期 fencing 提交被拒绝

- **WHEN** 持有过期 fencing 令牌的执行方提交决策
- **THEN** 提交被拒绝并返回 stale-fence 错误,至多一个版本被接受

#### Scenario: 存储不可用显式失败

- **WHEN** 提交时存储不可用
- **THEN** 调用方收到显式存储错误,无任何"提交成功"假象

#### Scenario: 双实现通过同一契约套件

- **WHEN** 参数化契约套件分别以 SQL 实现与 InMemory 实现运行提交/幂等/冲突/fencing 用例
- **THEN** 两组全部通过,套件位于 hecate-durable 测试内且两个实现均已注册

### Requirement: 决策提交校验权威租约并保留治理事件

SQL 决策提交 SHALL 在同一事务内复用现有 LeaseManager,经权威 Task/Run 关联校验 owning Task 的现有 dispatch lease_key、holder、精确 token 与权威期限,首次提交新步骤和幂等重投均不得豁免;不得以决策行上的历史 token 充当租约真源。无租约、伪造的更大 token、无关作用域或失效句柄 SHALL 拒绝。决策与既有 durable outbox 中的接受事件 SHALL 同事务提交;事件失败 SHALL 不留下可见决策,重复提交 SHALL 不重复事件。Task 引用从既有权威 Run 关联解析,不得捏造。记录 SHALL 含明确 schema_version,格式不兼容或内容损坏不得作为未接受处理。应用回退 SHALL 保留决策事实,不得以 destructive downgrade 删表。

#### Scenario: 过期 owner 首次提交另一步骤

- **WHEN** 旧 owner 的租约已到期或被接管,即使目标步骤尚无记录
- **THEN** 提交仍拒绝,不留下决策或接受事件

#### Scenario: 事件写入失败

- **WHEN** 接受事件写入失败或同事务随后失败
- **THEN** 决策行与事件同时回滚,后续 lookup 不返回该未提交决策

#### Scenario: 幂等接受不重复事件

- **WHEN** 当前合法 owner 重投相同内容
- **THEN** 返回原记录且接受事件仍只有一条

#### Scenario: 应用版本回退不擦除决策

- **WHEN** 尝试通过本 revision 的 downgrade 删除已接受决策存储
- **THEN** 破坏性回退明确拒绝,表与事实保留
