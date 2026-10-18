# Spec Delta — durable-execution-contract

## MODIFIED Requirements

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

## ADDED Requirements

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
