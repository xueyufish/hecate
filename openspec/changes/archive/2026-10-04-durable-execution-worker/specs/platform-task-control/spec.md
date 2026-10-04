# Spec Delta

## MODIFIED Requirements

### Requirement: 平台任务提交经幂等键绑定服务端上下文

`POST /api/tasks` SHALL 接受业务目标、执行目标(agent/deployment 引用)、输入与可选预算/截止时间,并在服务端验证的调用主体与 workspace 作用域内处理幂等键:键绑定主体、workspace 与规范化请求摘要。相同键、相同摘要的重复提交 SHALL 返回首次提交的 Task/Run 关联,不产生第二次执行;相同键、不同摘要 SHALL 返回冲突错误,原有关联不被改写;键绑定的主体/workspace 与验证上下文不一致 SHALL 被拒绝。提交成功 SHALL 返回持久化的 task_id 与 run_id;请求可携带等待参数以获取同步等待视图(在 Task 到达终态或超时后返回),未携带时提交立即返回 queued 状态。提交后任务 SHALL 经持久派发 worker 投递执行(`durable-execution-worker` 能力):worker 崩溃或进程重启后,未终态任务经对账继续(queued 重派、过期租约以新 fencing token 重派),平台据此宣称跨重启的后台执行保证;已终态任务不被重派。

#### Scenario: 同键同摘要幂等

- **WHEN** 同一主体以相同幂等键与相同请求体重复提交
- **THEN** 响应返回与首次提交相同的 task_id/run_id,系统内不产生第二个 Task

#### Scenario: 同键异摘要冲突

- **WHEN** 同一主体以相同幂等键提交不同请求体
- **THEN** 响应为冲突错误,首次提交的关联不被改写

#### Scenario: 跨主体键作用域拒绝

- **WHEN** 幂等键被绑定主体之外的验证上下文重放
- **THEN** 提交被拒绝,不返回原关联

#### Scenario: 提交后进程重启执行继续

- **WHEN** 提交成功且派发进行中时平台进程重启
- **THEN** 该任务经对账继续执行并到达终态或显式待对账,不静默丢失,不产生第二次同义执行

### Requirement: 调度器触发经 executor registry 真实执行

定时任务调度器的执行路径 SHALL 将触发分派给 executor registry 按任务目标(agent/workflow)解析的执行器,执行器的结果映射为执行记录的成功/失败与错误信息;不执行任何实际工作的空转路径 MUST 删除。分派 SHALL 尊重既有并发上限与多节点咨询锁语义;执行器失败 MUST 记录失败状态与错误信息,不吞错为成功。调度器的 job 状态(触发/投递/重试记录)与平台任务状态 SHALL 各有单一写入方:调度器 MUST NOT 改写任务生命周期投影,平台 MUST NOT 改写调度器 job 记录;调度器重试与 worker 派发的并发 SHALL 经提交幂等键与租约 fencing 仲裁,旧 ownership 的迟到写入被拒绝并留事件。

#### Scenario: 调度触发真实执行

- **WHEN** 一个绑定 agent 的启用定时任务到达触发时间
- **THEN** 调度器经 registry 将执行分派给 agent 执行器,执行记录反映执行器的真实结果

#### Scenario: 执行器失败如实记录

- **WHEN** registry 分派的执行器返回失败
- **THEN** 该次执行记录为 failed 并携带错误信息,不被记为 success

#### Scenario: 旧 ownership 迟到写入被拒绝

- **WHEN** 调度器重试与 worker 重派并发,过期租约的旧执行方提交结果
- **THEN** fencing 校验拒绝该写入并保留事件,权威状态不被覆盖,不产生重复业务动作

### Requirement: seam 绑定经 composition 单点切换

平台对持久执行接缝(任务状态存储、命令记录器、Action 台账)的消费 SHALL 经 composition 的进程级绑定点:开发/测试默认绑定 InMemory Stub;生产绑定持久执行核心包的 SQL 参考存储(`SqlDurableStore`,三接缝共享同一引擎与事务域),平台 PostgreSQL 直连 adapter 退役;每个生产实现 MUST 注册进参数化契约套件并全量通过既有断言。绑定点 MUST 是唯一的,API 与服务层代码不感知实现替换。SQL 参考存储的每次调用 SHALL 使用独立短事务 session,不在并发请求间共享会话。

#### Scenario: 生产绑定通过契约套件

- **WHEN** `SqlDurableStore` 注册进参数化契约套件
- **THEN** 既有全部契约断言(生命周期迁移、回执状态、幂等键、领取仲裁)对该实现原样通过

#### Scenario: 开发绑定不依赖数据库

- **WHEN** 开发/测试 profile 下任务控制服务运行
- **THEN** 生命周期与命令回执经 InMemory Stub 工作,不要求新表存在

#### Scenario: 台账来源为生产实现

- **WHEN** postgres 后端绑定生效
- **THEN** 三个接缝均来自 `SqlDurableStore`,绑定元数据如实标注台账来源,API 不再标注 stub 台账

## ADDED Requirements

### Requirement: 任务等待人工输入或审批为持久等待并可恢复

任务控制面 SHALL 支持进入 `waiting_input`/`waiting_approval` 的持久等待:等待状态与输入/审批契约引用、单次消费回调 token、期限一并持久化;`waiting_input` 经 `provide_input` 命令、`waiting_approval` 经 `resume` 命令(审批决定语义,授权策略归 step7)凭有效 token 唤醒,输入随任务状态持久化并在重派时回放;token 已消费、已过期、任务已终态或输入与等待契约不符 SHALL 拒绝并记录;进程重启后等待任务保持等待状态且可被唤醒。审批的策略判定、参数绑定校验与职责分离归授权域(step7),本能力只保证等待的持久化、真实回执与恢复。

#### Scenario: 等待跨重启

- **WHEN** 任务等待输入期间平台进程重启
- **THEN** 任务保持 `waiting_input`,有效 token 唤醒后按持久化输入重放派发

#### Scenario: token 单次消费

- **WHEN** 同一回调 token 被重复使用
- **THEN** 第二次唤醒被拒绝并留下拒绝记录,任务状态不被二次改写

#### Scenario: 迟到输入拒绝

- **WHEN** 输入在任务到达终态后到达
- **THEN** 唤醒被拒绝并记录,不复活已终态任务
